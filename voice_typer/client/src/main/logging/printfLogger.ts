/**
 * Printf-style structured logger for the Electron main process.
 *
 * Extracted from the original `main/logging.ts` (DT-35 Phase 4.5
 * spaghetti split). Owns:
 *
 *   - `log` — printf-style API:
 *     `log.info("[BUBBLE] creating window at", x, y)`. Routes through
 *     colored stdout (ANSI `[INFO]`/`[WARN]`/`[ERROR]` prefixes) and
 *     tees WARN/ERROR to `<userData>/electron-runtime.log` with 5 MiB
 *     rotation. Uses the top-level `import { app } from "electron"`
 *     (AC-117 — the previous lazy-`require` was dead code; the ESM
 *     import already forces the module to resolve at load time, so
 *     tests must mock `electron` via `vi.mock`).
 *   - `LogShape` — the public type of the `log` object (consumed by
 *     tests + type-only importers).
 *   - `getRuntimeLogPath()` — ER-63 memoized resolver for
 *     `electron-runtime.log` (cached for the process lifetime;
 *     `_resetRuntimeLogPathForTest` clears the cache for tests).
 *   - `_getRuntimeLogPathForTest()` / `_resetRuntimeLogPathForTest()`
 *     — test-only exports for asserting memoization behavior.
 *
 * Local (non-exported) helpers:
 *   - `_runtimeLogPath` — the memoization slot.
 *   - `formatArgsForFile()` — coerces console-style args to a single
 *     space-joined string for file output.
 *   - `writeStdout()` — writes one line to stdout with the standard
 *     timestamp + level prefix.
 *   - `mainRuntimeLogger` — the persistent runtime log file writer
 *     (appends WARN/ERROR to `electron-runtime.log` via
 *     `appendLogLine`).
 *
 * Imports: `path`, Electron's `app`, the color constants
 * (`INFO_CLR` / `WARN_CLR` / `ERROR_CLR` / `RESET`), the
 * `RUNTIME_LOG_MAX_BYTES` cap, `ts` + `appendLogLine` from
 * `./rotation`, and `PERSIST_INFO` + `appendLifecycleLine` from
 * `./structuredLogger` (so the printf-style `log.info` can mirror the
 * opt-in INFO persistence wired up on `logger.info`).
 */
import path from "node:path";
import { app } from "electron";

import { ERROR_CLR, INFO_CLR, RESET, WARN_CLR } from "./colors";
import { RUNTIME_LOG_MAX_BYTES } from "./constants";
import { appendLogLine, redactPii, ts } from "./rotation";
import { appendLifecycleLine, PERSIST_INFO } from "./structuredLogger";

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
 * `import { app } from "electron"` directly.
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

export function getRuntimeLogPath(): string | null {
	// ER-63: cache hit — return the previously resolved path (or null
	// if a prior call found Electron unavailable). Avoids the
	// `app.getPath` round-trip on every `log.warn` / `log.error`
	// invocation.
	if (_runtimeLogPath !== undefined) {
		return _runtimeLogPath;
	}
	try {
		// The top-level `import { app } from "electron"` is the canonical
		// binding (vitest 4 intercepts static imports but NOT dynamic
		// `require("electron")` in ESM-transpiled modules — so the previous
		// lazy-require pattern was opaque to the mock system and untestable).
		// The `?? process.cwd()` fallback preserves the original behaviour
		// for non-Electron contexts.
		const userDataDir = app?.getPath?.("userData") ?? process.cwd();
		_runtimeLogPath = path.join(userDataDir, "electron-runtime.log");
	} catch {
		// Edge case: `app.getPath` can throw if the userData dir
		// is unset/unavailable (e.g. very early in test setup
		// where the Electron mock doesn't yet implement getPath).
		// Return null so `mainRuntimeLogger.write` silently
		// no-ops — the stdout tee already captured the message.
		_runtimeLogPath = null;
	}
	return _runtimeLogPath;
}

/**
 * ER-63: test-only export of the memoized path resolver. Exposed so
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
 *
 * XZ-LOG-03: every arg is run through `redactPii` (PII / API-key / URL
 * credential redaction) before joining so the file log never leaks
 * user-spoken text or secrets. The stdout tee (in `writeStdout`) also
 * routes through this helper so stdout + file get the same redaction
 * (no asymmetric leak where stdout has the raw text but the file
 * doesn't, or vice versa). Idempotent on already-redacted text so
 * callers that pre-redact (e.g. `cleanConsoleMsg` chains) don't
 * double-redact.
 */
function formatArgsForFile(args: unknown[]): string {
	return args
		.map((a) => {
			if (a instanceof Error) {
				return redactPii(a.stack ?? `${a.name}: ${a.message}`);
			}
			if (typeof a === "string") return redactPii(a);
			try {
				return redactPii(JSON.stringify(a));
			} catch {
				return redactPii(String(a));
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
