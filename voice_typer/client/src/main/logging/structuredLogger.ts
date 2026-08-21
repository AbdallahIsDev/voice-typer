/**
 * Message-first structured logger for the Electron main process.
 *
 * Extracted from the original `main/logging.ts` (spaghetti
 * split). Owns:
 *
 *   - `logger` — message-first API:
 *     `logger.info("TCP connected", { port: 7001 })`. Writes to
 *     `<config-dir>/logs/electron-main.log` with 5 MiB rotation. DEBUG is
 *     dev-only (gated by `!app.isPackaged`). INFO is dev-only in file
 *     output by default (production writes only WARN/ERROR to file).
 *     Set `VOICE_TYPER_ELECTRON_INFO_LOG=1` to opt in to production INFO
 *     persistence (routes to `electron-lifecycle.log`).
 *   - `mainLogPath()` — resolves `<config-dir>/logs/electron-main.log`.
 *   - `rendererErrorsLogPath()` — resolves
 *     `<config-dir>/logs/electron-renderer-errors.log` (consumed by the
 *     main-window `console-message` handler via `window-handlers.ts`).
 *   - `lifecycleLogPath()` + `appendLifecycleLine()` — the opt-in
 *     INFO persistence target (1 MiB × 1 backup).
 *   - `PERSIST_INFO` — the env-var-gated flag consumed by both this
 *     module's `logger.info` AND by `printfLogger.ts`'s `log.info`
 *     (the printf-style logger mirrors the opt-in here).
 *   - `formatLine` — local helper that renders a file-friendly line
 *     (ISO-8601 timestamp + level tag + JSON-stringified args).
 *
 * Imports: `path`, Electron's `app` (for `isPackaged` gating),
 * `computeConfigDir` from `../config-dir`, and
 * `appendLogLine` from `./rotation`.
 */
import path from "node:path";
import { app } from "electron";

import { computeConfigDir } from "../config-dir";
import {
	appendLogLine,
	fileTimestamp,
	recordLoggingFailure,
	redactPii,
} from "./rotation";

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
//
// Exported (not in the public barrel) so `printfLogger.ts` can mirror
// the opt-in on its `log.info` without re-reading `process.env`.
export const PERSIST_INFO = process.env.VOICE_TYPER_ELECTRON_INFO_LOG === "1";

/**
 * Resolve the per-process session ID for the Electron log
 * line prefix.
 *
 * The ID is sourced from ``process.env.VOICE_TYPER_SESSION_ID`` (set
 * by ``bootstrap.ts::generateSessionNonce`` at app startup, OR by a
 * parent process like a test harness). When unset (e.g. a fresh test
 * that hasn't called ``bootstrapRuntime``), the bracket renders as
 * ``[--------]`` (8 dashes) — mirroring the Python side's
 * ``_SessionFilter`` fallback (``log.py:474 / 567`` uses the same
 * 8-dash placeholder). This keeps the bracket shape stable so log
 * parsers can rely on a consistent ``[xxxxxxxx]`` token even when the
 * ID isn't yet known.
 *
 * Memoized at module init: the env var is read ONCE, then the cached
 * value is reused on every ``formatLine`` call. ``VOICE_TYPER_SESSION_ID``
 * is set by ``bootstrap.ts`` BEFORE any ``logger.*`` call lands (the
 * bootstrap is the first thing ``app.whenReady()`` runs), so the
 * memoization is safe. If the env var is set LATER (e.g. by a test
 * that sets it after import), the memoized value would be stale — but
 * no production code path does this, and the test seam
 * ``_getSessionIdForTest`` (below) lets tests override the memo.
 */
const SESSION_ID_PLACEHOLDER = "--------";
let _sessionId: string | undefined;

function getSessionId(): string {
	if (_sessionId === undefined) {
		_sessionId =
			process.env.VOICE_TYPER_SESSION_ID?.trim() || SESSION_ID_PLACEHOLDER;
	}
	return _sessionId;
}

/**
 * Test seam: override the memoized session ID. Pass ``undefined`` to
 * force re-reading ``process.env.VOICE_TYPER_SESSION_ID`` on the next
 * ``formatLine`` call. Exported (not in the public barrel) so tests
 * can pin a stable bracket without depending on env-var mutation.
 */
export function _setSessionIdForTest(id: string | undefined): void {
	_sessionId = id;
}

// ─── Memoized `<config-dir>/logs/<filename>` resolver ────────────────────
//
// Mirror the `getRuntimeLogPath()` pattern in `./printfLogger.ts`:
// `computeConfigDir()` is non-trivial (platform-specific dir computation
// + legacy `~/.voice-typer` probe), but the result is stable for the
// process lifetime — the config dir never moves after
// `bootstrapRuntime()`'s `setupUserData()` step. Caching the resolved
// path eliminates the per-`logger.warn` / `logger.error` round-trip
// (previously every WARN/ERROR line re-resolved `mainLogPath()` via
// `app.getPath`).
//
// Previously three near-identical resolvers (`mainLogPath` /
// `lifecycleLogPath` / `rendererErrorsLogPath`) each carried their own
// `let _fooLogPath: string | undefined;` slot AND their own copy of
// the same `app?.getPath?.("userData") ?? process.cwd()` + try/catch
// resolution body. The deduplication below collapses the three slots
// into a single `Map<string, string>` keyed by filename, with one
// shared `memoizeUserDataPath(filename)` helper. Per-filename cache
// keys preserve the test contract that each resolver triggers
// resolution exactly once on its first call (independent memoization
// — see `log-path-memoization.test.ts`).
//
// The cache uses `undefined` as the "not yet computed" sentinel (via
// `Map.get` returning `undefined` on miss). On the first call, the
// path is resolved (with a `process.cwd()` fallback if config-dir
// resolution fails, mirroring `getRuntimeLogPath`'s try/catch —
// preserving the existing `string` return type so callers like
// `appendLogLine` don't need to handle `string | null`). Subsequent
// calls return the cached value without re-touching `computeConfigDir`.
//
// `_resetMainLogPathForTest()` clears the cache so unit tests can
// re-resolve after swapping the config-dir mock (matches the
// `_resetRuntimeLogPathForTest()` convention in printfLogger — same
// name shape, same "one reset covers the module's memoization state"
// ergonomics). The legacy name is preserved so existing test imports
// keep working.
const _userDataPathCache = new Map<string, string>();

/**
 * Resolve `<config-dir>/logs/<filename>` with memoization keyed by filename.
 * Used by `mainLogPath` / `lifecycleLogPath` / `rendererErrorsLogPath`
 * so the three resolvers share one cache + one resolution body instead
 * of three near-identical copies. Each filename gets its own cache
 * entry, so the three resolvers memoize independently — calling
 * `mainLogPath()` does NOT pre-populate `lifecycleLogPath()`'s slot.
 *
 * First call per filename resolves via `computeConfigDir()` + `/logs`;
 * subsequent calls return the cached value without re-touching
 * config-dir resolution. If `computeConfigDir` throws (degenerate
 * environment), the fallback is
 * `path.join(process.cwd(), "logs", filename)` — cached so subsequent
 * calls don't re-attempt.
 */
function memoizeUserDataPath(filename: string): string {
	const cached = _userDataPathCache.get(filename);
	if (cached !== undefined) return cached;
	let resolved: string;
	try {
		const logsDir = path.join(computeConfigDir(), "logs");
		resolved = path.join(logsDir, filename);
	} catch {
		resolved = path.join(process.cwd(), "logs", filename);
	}
	_userDataPathCache.set(filename, resolved);
	return resolved;
}

/**
 * Test seam: clear the memoized `mainLogPath` / `lifecycleLogPath` /
 * `rendererErrorsLogPath` slots so the next call to each re-resolves
 * via `computeConfigDir()` + `/logs`. Exported (not in the public
 * barrel) so unit tests can assert memoization behavior — call counts
 * on the `computeConfigDir` mock, cache-hit return values, and the
 * `_resetMainLogPathForTest()` → re-resolve cycle. Production code
 * must NOT call this — the paths are intended to memoize for the
 * process lifetime (the config dir does not move after
 * `bootstrapRuntime()`'s `setupUserData()` step).
 */
export function _resetMainLogPathForTest(): void {
	_userDataPathCache.clear();
}

/**
 * Resolve the path to `electron-lifecycle.log` under the Electron
 * config-dir `logs/` subdir. Kept separate from `mainLogPath` so the
 * opt-in INFO stream never competes with the WARN/ERROR stream for the
 * 5 MiB `electron-main.log` rotation window.
 */
export function lifecycleLogPath(): string {
	return memoizeUserDataPath("electron-lifecycle.log");
}

/**
 * Append a single INFO line to `electron-lifecycle.log` with a
 * 1 MiB rotation (single `.1` backup). Best-effort — any I/O error is
 * swallowed so logging can never crash the caller's code path.
 *
 * Mirrors the rotate-then-append pattern of `appendLogLine` but
 * uses a tighter 1 MiB cap (vs the 5 MiB cap on `electron-main.log`)
 * because the INFO stream is higher-volume and would otherwise push
 * WARN/ERROR context out of the smaller log too quickly. Total disk
 * usage is bounded at ~2 MiB worst case (active file + `.1` backup).
 *
 * The rotation logic was previously inlined here (a `statSync`
 * + `renameSync` + `appendFileSync` sequence). This bypassed the
 * file-size cache (`fileSizeCache.ts`) — every INFO write did
 * a synchronous `fs.statSync` on the main process event loop, the
 * exact perf bug the cache was designed to eliminate for
 * `electron-main.log`. On a busy session (30 Hz bubble events) under
 * `VOICE_TYPER_ELECTRON_INFO_LOG=1`, this was 30 `statSync` calls/sec
 * on the main thread. Replacing the inline rotation with a call to
 * `appendLogLine(p, line, 1024 * 1024)` routes the writes through the
 * file-size cache and the shared rotation primitive. The `mode: 0o600`
 * parity is also picked up for free via `appendLogLine`.
 *
 * Exported (not in the public barrel) so `printfLogger.ts`'s `log.info`
 * can route its opt-in INFO persistence through the same writer.
 */
export function appendLifecycleLine(
	level: string,
	msg: string,
	args: unknown[],
): void {
	try {
		const tsStr = fileTimestamp();
		// Redact PII / API keys / URL credentials from the
		// message + args before persisting to the lifecycle
		// log. Shares the same `redactArgsForFile` helper as
		// `formatLine` so the opt-in INFO persistence stream
		// never leaks dictated-text fragments or secrets that
		// the renderer may have logged via `logger.info`, and
		// so the two tees (`electron-main.log` via `formatLine`
		// and `electron-lifecycle.log` via this function) never
		// drift in their redaction / formatting.
		const formatted = redactArgsForFile(msg, args);
		const line = `${tsStr} [${level.toUpperCase()}] ${formatted}\n`;
		const p = lifecycleLogPath();
		// Delegate to the shared `appendLogLine` helper so the
		// file-size cache eliminates the per-write `statSync`
		// and the rotation logic stays in one place. The 1 MiB cap
		// matches the prior inline rotation's threshold.
		appendLogLine(p, line, 1024 * 1024);
	} catch (e) {
		// Never let logging crash the app — but surface the failure so a
		// misconfigured lifecycle-log path (read-only dir, perm regression)
		// is visible in the dev console instead of silently swallowed.
		console.warn("[logging] appendLifecycleLine failed:", e);
		// Record the failure to the in-memory logging-health ring buffer
		// so an orchestrator (or future IPC handler) can surface
		// "logging degraded" via `getLoggingHealth()`. The lifecycle-log
		// path may not have been resolved yet (the try block above calls
		// `lifecycleLogPath()` at the line just before `appendLogLine` —
		// if that call itself threw, `p` is out of scope here). Record
		// with an empty path; the `operation` label is enough for the
		// orchestrator to surface the degradation to the user.
		recordLoggingFailure("", "appendLifecycleLine", e);
	}
}

type Level = "debug" | "info" | "warn" | "error";

/**
 * Redact PII / API keys / URL credentials from `msg` + `args` and
 * join them into a single space-separated string for file output.
 * Mirrors `printfLogger.ts::formatArgsForFile` (single-format
 * discipline — the formatted string is shared between the file tee
 * and any other consumer).
 *
 * Used by both `formatLine` (the `electron-main.log` writer) and
 * `appendLifecycleLine` (the opt-in `electron-lifecycle.log` writer)
 * so the two tees never drift in their redaction / formatting —
 * pre-extraction they each carried their own duplicate copy of the
 * same redact-and-format block.
 *
 * Errors are stringified with their stack (when available) so the
 * file log preserves the same detail as stdout. Non-stringifiable
 * values fall back to `String(value)` to never throw. Idempotent on
 * already-redacted text so callers that pre-redact (e.g. via
 * `cleanConsoleMsg` chains) don't double-redact.
 */
function redactArgsForFile(msg: string, args: unknown[]): string {
	const safeMsg = redactPii(msg);
	if (args.length === 0) return safeMsg;
	return `${safeMsg} ${args
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
		.join(" ")}`;
}

/**
 * Format a log line for the file. Always ends with `\n` so `tail -f`
 * shows lines as they're written.
 *
 * Prepends the ``[session_id]`` bracket after the timestamp
 * so operators can grep across Rust / Python / Electron logs for the
 * same bracket to reconstruct a cross-process timeline. Mirrors the
 * Python side's ``_FileFormatter`` / ``_ConsoleFormatter`` (which
 * inject the same bracket via ``_SessionFilter``).
 */
function formatLine(level: Level, msg: string, args: unknown[]): string {
	const tsStr = fileTimestamp();
	const sessionId = getSessionId();
	const formatted = redactArgsForFile(msg, args);
	return `${tsStr} [${sessionId}] [${level.toUpperCase()}] ${formatted}\n`;
}

/**
 * Resolve the path to `electron-main.log` under the config-dir
 * `logs/` subdir.
 *
 * Originally module-private in `main/logging.ts` ("verified zero external
 * importers — only used internally by `logger.warn` / `logger.error` /
 * `logger.debug` / `logger.info`"). Exported from this split module so
 * the barrel can re-export it; external consumers may now read it
 * directly, but no behavior change is implied.
 *
 * Memoized for the process lifetime (see the block comment on
 * `_mainLogPath` above). The first call resolves via
 * `computeConfigDir()` + `/logs`; subsequent calls return the cached
 * value without re-touching config-dir resolution. If resolution throws
 * (degenerate environment), the fallback is
 * `path.join(process.cwd(), "logs", "electron-main.log")` — cached so
 * subsequent calls don't re-attempt.
 */
export function mainLogPath(): string {
	return memoizeUserDataPath("electron-main.log");
}

/**
 * Resolve the path to `electron-renderer-errors.log` under the
 * config-dir `logs/` subdir. The main-window `console-message` handler
 * appends level>=3 (ERROR) renderer messages to this file so support
 * staff can see renderer crashes without fishing through DevTools.
 *
 * Memoized for the process lifetime (see the block comment on
 * `_mainLogPath` above). The first call resolves via
 * `computeConfigDir()` + `/logs`; subsequent calls return the cached
 * value without re-touching config-dir resolution. If resolution throws
 * (degenerate environment), the fallback is
 * `path.join(process.cwd(), "logs", "electron-renderer-errors.log")` —
 * cached so subsequent calls don't re-attempt.
 */
export function rendererErrorsLogPath(): string {
	return memoizeUserDataPath("electron-renderer-errors.log");
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
		// Dev-only stdout + file write: in packaged builds
		// stdout/stderr are closed (no terminal attached) so
		// `console.info` is a no-op. Skipping the call in
		// production avoids the wasted `console.info` invoke
		// (and any console-formatter overhead Electron's
		// renderer-devtools bridge might attach). `warn` and
		// `error` (below) intentionally keep their `console.*`
		// calls OUTSIDE the gate — their stderr output may be
		// captured by Electron's crash reporter even in
		// packaged builds, so they must fire unconditionally.
		if (!app.isPackaged) {
			console.info(msg, ...args);
			// Dev: persist INFO so the dev can grep the file.
			appendLogLine(mainLogPath(), formatLine("info", msg, args));
		}
		// Production: INFO is too chatty for the rotating file —
		// it would push WARN/ERROR out of the 5 MB window too
		// fast. Skip the file write.
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
