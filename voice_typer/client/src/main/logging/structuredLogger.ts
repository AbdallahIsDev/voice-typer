/**
 * Message-first structured logger for the Electron main process.
 *
 * Extracted from the original `main/logging.ts` (spaghetti
 * split). Owns:
 *
 *   - `logger` — message-first API:
 *     `logger.info("TCP connected", { port: 7001 })`. Writes to
 *     `<userData>/electron-main.log` with 5 MiB rotation. DEBUG is
 *     dev-only (gated by `!app.isPackaged`). INFO is dev-only in file
 *     output by default (production writes only WARN/ERROR to file).
 *     Set `VOICE_TYPER_ELECTRON_INFO_LOG=1` to opt in to production INFO
 *     persistence (routes to `electron-lifecycle.log`).
 *   - `mainLogPath()` — resolves `<userData>/electron-main.log`.
 *   - `rendererErrorsLogPath()` — resolves
 *     `<userData>/electron-renderer-errors.log` (consumed by the
 *     main-window `console-message` handler via `window-handlers.ts`).
 *   - `lifecycleLogPath()` + `appendLifecycleLine()` — the opt-in
 *     INFO persistence target (1 MiB × 1 backup).
 *   - `PERSIST_INFO` — the env-var-gated flag consumed by both this
 *     module's `logger.info` AND by `printfLogger.ts`'s `log.info`
 *     (the printf-style logger mirrors the opt-in here).
 *   - `formatLine` — local helper that renders a file-friendly line
 *     (ISO-8601 timestamp + level tag + JSON-stringified args).
 *
 * Imports: `fs`, `path`, Electron's `app`, and `appendLogLine` from
 * `./rotation`.
 */
import fs from "node:fs";
import path from "node:path";
import { app } from "electron";

import { appendLogLine, recordLoggingFailure, redactPii } from "./rotation";

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

// ─── Memoization slots for the three Electron log file paths ────────
//
// Mirror the `getRuntimeLogPath()` pattern in `./printfLogger.ts`:
// `app.getPath("userData")` is non-trivial (Electron lazy-loads its
// `app` module and `getPath` does a platform-specific dir computation),
// but the result is stable for the process lifetime — `setupUserData()`
// in `bootstrap.ts` runs exactly once at startup BEFORE any `logger.*`
// call could fire. Caching the resolved path eliminates the per-
// `logger.warn` / `logger.error` round-trip (previously every WARN/ERROR
// line re-resolved `mainLogPath()` via `app.getPath`).
//
// The cache uses `undefined` as the "not yet computed" sentinel. On the
// first call, the path is resolved (with a `process.cwd()` fallback if
// Electron is unavailable, mirroring `getRuntimeLogPath`'s
// `?? process.cwd()` nullish-coalesce AND its try/catch — preserving
// the existing `string` return type so callers like `appendLogLine`
// don't need to handle `string | null`). Subsequent calls return the
// cached value without touching `app.getPath` again.
//
// `_resetMainLogPathForTest()` clears all three slots so unit tests
// can re-resolve after swapping the Electron mock (matches the
// `_resetRuntimeLogPathForTest()` convention in printfLogger — same
// name shape, same "one reset covers the module's memoization state"
// ergonomics).
let _mainLogPath: string | undefined;
let _lifecycleLogPath: string | undefined;
let _rendererErrorsLogPath: string | undefined;

/**
 * Test seam: clear the memoized `mainLogPath` / `lifecycleLogPath` /
 * `rendererErrorsLogPath` slots so the next call to each re-resolves
 * via `app.getPath("userData")`. Exported (not in the public barrel)
 * so unit tests can assert memoization behavior — call counts on the
 * `electron` mock's `app.getPath`, cache-hit return values, and the
 * `_resetMainLogPathForTest()` → re-resolve cycle. Production code
 * must NOT call this — the paths are intended to memoize for the
 * process lifetime (the userData dir does not move after
 * `bootstrapRuntime()`'s `setupUserData()` step).
 */
export function _resetMainLogPathForTest(): void {
	_mainLogPath = undefined;
	_lifecycleLogPath = undefined;
	_rendererErrorsLogPath = undefined;
}

/**
 * Resolve the path to `electron-lifecycle.log` under the Electron
 * userData dir. Kept separate from `mainLogPath` so the opt-in
 * INFO stream never competes with the WARN/ERROR stream for the 5 MiB
 * `electron-main.log` rotation window.
 */
export function lifecycleLogPath(): string {
	if (_lifecycleLogPath === undefined) {
		try {
			const userDataDir = app?.getPath?.("userData") ?? process.cwd();
			_lifecycleLogPath = path.join(userDataDir, "electron-lifecycle.log");
		} catch {
			_lifecycleLogPath = path.join(process.cwd(), "electron-lifecycle.log");
		}
	}
	return _lifecycleLogPath;
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
		const tsStr = new Date().toISOString();
		// Redact PII / API keys / URL credentials from
		// the message + args before persisting to the lifecycle
		// log (mirrors `formatLine`'s redaction so the opt-in
		// INFO persistence stream never leaks dictated-text
		// fragments or secrets that the renderer may have logged
		// via `logger.info`).
		const safeMsg = redactPii(msg);
		const formatted =
			args.length > 0
				? `${safeMsg} ${args
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
						.join(" ")}`
				: safeMsg;
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
	const tsStr = new Date().toISOString();
	const sessionId = getSessionId();
	// Redact PII / API keys / URL credentials from the
	// message + args before joining so the file log never leaks
	// user-spoken text or secrets. Mirrors the parity already in
	// `printfLogger.ts::formatArgsForFile`. Idempotent on already-
	// redacted text so callers that pre-redact (e.g. via
	// `cleanConsoleMsg` chains) don't double-redact.
	const safeMsg = redactPii(msg);
	const formatted =
		args.length > 0
			? `${safeMsg} ${args
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
					.join(" ")}`
			: safeMsg;
	return `${tsStr} [${sessionId}] [${level.toUpperCase()}] ${formatted}\n`;
}

/**
 * Resolve the path to `electron-main.log` under the Electron userData dir.
 *
 * Originally module-private in `main/logging.ts` ("verified zero external
 * importers — only used internally by `logger.warn` / `logger.error` /
 * `logger.debug` / `logger.info`"). Exported from this split module so
 * the barrel can re-export it; external consumers may now read it
 * directly, but no behavior change is implied.
 *
 * Memoized for the process lifetime (see the block comment on
 * `_mainLogPath` above). The first call resolves via
 * `app.getPath("userData")`; subsequent calls return the cached value
 * without re-touching Electron. If `app.getPath` throws (Electron
 * unavailable in a degenerate test environment), the fallback is
 * `path.join(process.cwd(), "electron-main.log")` — cached so
 * subsequent calls don't re-attempt.
 */
export function mainLogPath(): string {
	if (_mainLogPath === undefined) {
		try {
			const userDataDir = app?.getPath?.("userData") ?? process.cwd();
			_mainLogPath = path.join(userDataDir, "electron-main.log");
		} catch {
			_mainLogPath = path.join(process.cwd(), "electron-main.log");
		}
	}
	return _mainLogPath;
}

/**
 * Resolve the path to `electron-renderer-errors.log` under the Electron
 * userData dir. The main-window `console-message` handler
 * appends level>=3 (ERROR) renderer messages to this file so support
 * staff can see renderer crashes without fishing through DevTools.
 *
 * Memoized for the process lifetime (see the block comment on
 * `_mainLogPath` above). The first call resolves via
 * `app.getPath("userData")`; subsequent calls return the cached value
 * without re-touching Electron. If `app.getPath` throws (Electron
 * unavailable in a degenerate test environment), the fallback is
 * `path.join(process.cwd(), "electron-renderer-errors.log")` — cached
 * so subsequent calls don't re-attempt.
 */
export function rendererErrorsLogPath(): string {
	if (_rendererErrorsLogPath === undefined) {
		try {
			const userDataDir = app?.getPath?.("userData") ?? process.cwd();
			_rendererErrorsLogPath = path.join(
				userDataDir,
				"electron-renderer-errors.log",
			);
		} catch {
			_rendererErrorsLogPath = path.join(
				process.cwd(),
				"electron-renderer-errors.log",
			);
		}
	}
	return _rendererErrorsLogPath;
}

/**
 * GDPR Art. 17 erasure for the Electron main-process log files.
 *
 * The Python `service.delete_all_personal_data()` walks
 * `_GDPR_PERSONAL_FILES` / `_GDPR_PERSONAL_GLOBS` against the Python
 * backend's `_config_dir()` root only — it cannot reach Electron's
 * `app.getPath("userData")` (a DIFFERENT directory on disk). Per
 * the design contract, the Electron loggers have no PII redaction, so
 * dictated-text fragments may be present in these files.
 *
 * This helper unlinks every Electron-side log file the app writes:
 *
 *   * `<userData>/electron-main.log` (+ rotated `.1`..`.5` produced by
 *     `rotateIfNeeded` in `./rotation.ts`).
 *   * `<userData>/electron-renderer-errors.log` (no rotation — single
 *     file, overwritten by `appendLogLine` until the 5 MiB cap is
 *     reached, then truncated — but still unlinked here for symmetry).
 *
 * Returns an object the caller can merge into the Python
 * `delete_all_personal_data` response shape (erased / failed lists).
 *
 * Best-effort: per-file `OSError` (file locked by another process on
 * Windows, EBUSY on a rare mount point) is surfaced in `failed` rather
 * than aborting — matching the Python side's `for name in
 * _GDPR_PERSONAL_FILES: try path.unlink() except ...` discipline.
 *
 * NOT WIRED to an IPC handler yet — see `docs/privacy/gdpr-delete.md`
 * "Electron logs gap (known limitation)". The intended wiring
 * is a `deleteAllPersonalData` IPC handler that calls this helper AND
 * proxies to the Python `delete_all_personal_data` command, then
 * merges both responses for the renderer.
 */
export function deleteElectronPersonalDataLogs(): {
	erased: string[];
	failed: Record<string, string>;
} {
	const erased: string[] = [];
	const failed: Record<string, string> = {};
	const userData = app.getPath("userData");
	// Glob both the active log file and rotated backups. The rotation
	// module writes `electron-main.log.1`..`electron-main.log.5` (5 MiB
	// cap × 5 backups). `electron-renderer-errors.log` has no rotation
	// (single file), but the glob `electron-renderer-errors.log*` also
	// matches the bare name so a single loop covers both.
	const candidates = [
		...fs
			.readdirSync(userData)
			.filter(
				(name) =>
					name === "electron-main.log" ||
					name.startsWith("electron-main.log.") ||
					name === "electron-renderer-errors.log" ||
					name.startsWith("electron-renderer-errors.log.") ||
					// XE-20-3: electron-runtime.log (printfLogger's
					// mainRuntimeLogger — every WARN/ERROR) and
					// electron-lifecycle.log (appendLifecycleLine) were
					// previously omitted, leaving up to ~12 MiB of
					// PII-bearing log data after a GDPR erasure.
					name === "electron-runtime.log" ||
					name.startsWith("electron-runtime.log.") ||
					name === "electron-lifecycle.log" ||
					name.startsWith("electron-lifecycle.log."),
			)
			.map((name) => path.join(userData, name)),
	];
	for (const p of candidates) {
		try {
			fs.unlinkSync(p);
			erased.push(p);
		} catch (err) {
			// ENOENT is a no-op (file already gone — fresh install, or
			// a previous GDPR delete). Other errors surface in `failed`
			// so the renderer can tell the user to delete the file
			// manually (matches the Python side's "no silent swallows"
			// rule).
			if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
				failed[p] = `${(err as Error).name}: ${(err as Error).message}`;
			}
		}
	}
	return { erased, failed };
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
