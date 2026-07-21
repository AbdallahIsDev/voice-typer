/**
 * Runtime setup executed inside `app.whenReady()`.
 *
 * Extracted from `index.ts` (REF-2). `bootstrapRuntime()` performs:
 *   1. SEC-029 per-session nonce generation (stored in `state.sessionNonce`).
 *   2. NEW-PRIV-010 userData override so Electron and Python share one
 *      config directory.
 *   3. SEC-012 / NEW-SEC-002 Content-Security-Policy headers (HTTP).
 *   4. SEC-021 uncaughtException / unhandledRejection handlers with a
 *      crash log + 5-error circuit breaker (CR-9: log rotation +
 *      REVIEW-12 alignment + REVIEW-9 sliding window).
 */
import fs from "node:fs";
import path from "node:path";
import { app, dialog, session } from "electron";
import { mainT } from "./i18n";
import { DEFAULT_CRASH_LOG_MAX_BYTES, rotateIfNeeded } from "./logging";
import { computeConfigDir } from "./single_instance";
import { state } from "./state";

/**
 * SEC-029: generate a per-session nonce. Use crypto.randomUUID()
 * when available (Node 14.17+/Electron 12+), fall back to a
 * timestamp+random string. Stored in `state.sessionNonce` and tagged
 * onto every python-event so the renderer can reject replayed frames.
 */
function generateSessionNonce(): void {
	try {
		const cryptoMod = require("node:crypto") as { randomUUID?: () => string };
		state.sessionNonce =
			cryptoMod.randomUUID?.() ||
			`${Date.now()}-${Math.random().toString(36).slice(2)}`;
	} catch {
		state.sessionNonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
	}
}

/**
 * NEW-PRIV-010: unify Electron's userData directory with the Python
 * backend's config directory.  Previously these were two separate
 * directories:
 *   - Python: ~/.voice-typer (legacy) or platform-appropriate path
 *     (see voice_typer/server/config.py:_config_dir())
 *   - Electron: app.getPath('userData') which defaults to
 *     %APPDATA%/voice-typer-desktop (based on package.json "name")
 *
 * This caused user confusion ("where is my data?") and made GDPR
 * right-to-portability harder (two locations to scrub).  We now
 * explicitly set Electron's userData to match the Python config dir
 * so both sides read/write the same location.
 */
function setupUserData(): void {
	try {
		const configDir = computeConfigDir();
		// Ensure the directory exists before Electron tries to use it.
		try {
			fs.mkdirSync(configDir, { recursive: true });
		} catch {
			/* ignore */
		}
		app.setPath("userData", configDir);
		console.warn(`[MAIN] userData set to: ${configDir}`);
	} catch (e) {
		console.warn("[MAIN] Failed to override userData path:", e);
		// Non-fatal — Electron falls back to its default userData location.
	}
}

/**
 * SEC-012 / NEW-SEC-002: Content Security Policy (HTTP headers).
 *
 * CSP is also set via <meta> tags in index.html and bubble.html for
 * production file:// loads, but certain directives (frame-ancestors,
 * form-action) are only honored when delivered as actual HTTP headers.
 * Setting them here via Electron's onHeadersReceived ensures they're
 * properly enforced in dev mode (http://localhost:5173) and in production.
 *
 * In dev mode (app.isPackaged === false), Vite's dev server injects
 * inline scripts (React Refresh preamble + HMR client) and uses eval
 * for sourcemaps.  We add 'unsafe-inline' and 'unsafe-eval' only in
 * dev mode to allow these.  Production builds have no inline scripts
 * or eval, so the strict 'self' directive applies and inline event
 * handlers (onclick="...") remain blocked.
 */
function setupCsp(): void {
	const CSP = [
		"default-src 'self'",
		`script-src 'self'${app.isPackaged === false ? " 'unsafe-eval' 'unsafe-inline'" : ""}`,
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data:",
		"font-src 'self' data:",
		"media-src 'self' data:",
		"connect-src 'self' https://api.github.com",
		"frame-ancestors 'none'",
		"form-action 'none'",
		"base-uri 'self'",
	].join("; ");

	session.defaultSession.webRequest.onHeadersReceived(
		(
			details: Electron.OnHeadersReceivedListenerDetails,
			callback: (headers: Electron.HeadersReceivedResponse) => void,
		) => {
			callback({
				responseHeaders: {
					...details.responseHeaders,
					"Content-Security-Policy": [CSP],
				},
			});
		},
	);
}

/**
 * SEC-021: previously the uncaughtException handler just console.error'd
 * and continued, leaving the process in a half-broken state (locked
 * mutex, half-written config). We now log to file, count occurrences,
 * and exit non-zero after N consecutive errors so the user sees the
 * crash instead of a silent zombie.
 *
 * CR-9 (IMPL-7): the crash log used to grow unbounded — `appendFileSync`
 * with no rotation, no size cap. In a crash-looping renderer scenario
 * `electron-crashes.log` could reach hundreds of MB. We now:
 *   - Call `rotateIfNeeded()` before every append (1 MiB cap, single
 *     `.1` backup — see `logging.ts`).
 *   - Split the two event types into separate files so the crash log
 *     stays a useful signal: `electron-crashes.log` for
 *     `uncaughtException` only, `electron-rejections.log` for
 *     `unhandledRejection`. Both rotate independently.
 *
 * REVIEW-12 alignment: `unhandledRejection` previously did NOT count
 * toward `uncaughtCount`, so a half-broken state where every IPC call
 * rejected its promise could run forever without tripping the breaker.
 * Both event types now share the same counter.
 *
 * REVIEW-9 sliding window: a long-lived process that hits one error
 * every few days (e.g. a transient network race) would eventually
 * accumulate 5 errors across months of healthy operation and exit.
 * We reset `uncaughtCount` to 0 once the process has gone
 * `SLIDING_WINDOW_MS` (60s) without any error — this is long enough
 * to still catch a tight crash loop (5 errors in <60s → exit) but
 * short enough that isolated transient errors do not poison the
 * counter across restarts.
 *
 * `uncaughtCount` / `lastErrorAt` live INSIDE the `_installErrorHandlers`
 * closure rather than at module scope so that unit tests can spin up a
 * fresh factory per case without leaking state across the vitest worker.
 * In production this is functionally equivalent: `setupErrorHandlers`
 * is called exactly once from `bootstrapRuntime()` and the closure
 * lives for the entire process lifetime (same as module scope).
 */
const MAX_UNCAUGHT = 5;
const SLIDING_WINDOW_MS = 60_000;

/**
 * Test seam: returns the two log paths `setupErrorHandlers` would use.
 *
 * Exported so unit tests can target a temp directory without having to
 * mock `app.getPath("userData")`. The path computation itself is pure
 * (no I/O), so testing it directly is safe.
 */
export function _crashLogPaths(userDataDir: string): {
	crashLogPath: string;
	rejectionLogPath: string;
} {
	return {
		crashLogPath: path.join(userDataDir, "electron-crashes.log"),
		rejectionLogPath: path.join(userDataDir, "electron-rejections.log"),
	};
}

/**
 * Test seam + reusable factory: install the uncaughtException /
 * unhandledRejection handlers with explicit log paths and an injectable
 * exit hook. `setupErrorHandlers()` calls this with the production paths
 * and `process.exit`.
 *
 * Splitting the installable factory out of `setupErrorHandlers()` lets
 * unit tests:
 *   - point the log files at a `tmpdir()` instead of the real userData,
 *   - inject a mock exit hook so the test runner isn't killed when the
 *     breaker trips,
 *   - still exercise the real `process.on(...)` wiring and the real
 *     `rotateIfNeeded` + `appendFileSync` pipeline.
 *
 * Returns a `dispose()` so tests can remove the listeners between
 * cases — otherwise vitest's worker process accumulates handlers across
 * tests and the next test's `process.emit("uncaughtException", ...)`
 * would fire stale handlers from the previous test.
 *
 * @internal
 */
export function _installErrorHandlers(opts: {
	userDataDir: string;
	maxBytes?: number;
	exit?: (code: number) => void;
}): { dispose: () => void } {
	const { userDataDir, maxBytes = DEFAULT_CRASH_LOG_MAX_BYTES } = opts;
	const exit =
		opts.exit ??
		((code: number) => {
			process.exit(code);
		});
	const { crashLogPath, rejectionLogPath } = _crashLogPaths(userDataDir);

	let uncaughtCount = 0;
	let lastErrorAt = 0;

	const logEvent = (filePath: string, kind: string, err: unknown) => {
		try {
			rotateIfNeeded(filePath, maxBytes);
			const ts = new Date().toISOString();
			const line = `${ts} [${kind}] ${
				err instanceof Error ? (err.stack ?? err.message) : String(err)
			}\n`;
			fs.appendFileSync(filePath, line, { encoding: "utf-8" });
		} catch {
			// Logging is best-effort.
		}
	};

	const bumpCount = (): boolean => {
		const now = Date.now();
		// REVIEW-9 sliding window: if it has been more than
		// SLIDING_WINDOW_MS since the last error, the previous
		// errors are considered an isolated burst (different
		// root cause) — reset the counter before incrementing.
		if (lastErrorAt !== 0 && now - lastErrorAt > SLIDING_WINDOW_MS) {
			uncaughtCount = 0;
		}
		lastErrorAt = now;
		uncaughtCount++;
		return uncaughtCount >= MAX_UNCAUGHT;
	};

	const onUncaught = (err: unknown) => {
		console.error("[VT] uncaughtException:", err);
		logEvent(crashLogPath, "uncaughtException", err);
		if (bumpCount()) {
			console.error(
				`[VT] ${uncaughtCount} uncaught errors — exiting to avoid zombie state`,
			);
			try {
				dialog.showErrorBox(
					mainT("dialog.criticalError.title"),
					mainT("dialog.criticalError.body", {
						count: uncaughtCount,
						logPath: crashLogPath,
					}),
				);
			} catch {
				// dialog may not be available in headless mode
			}
			exit(1);
		}
	};

	const onRejection = (err: unknown) => {
		console.error("[VT] unhandledRejection:", err);
		logEvent(rejectionLogPath, "unhandledRejection", err);
		// REVIEW-12 alignment: a rejected promise leaves the app
		// in the same half-broken state as an uncaught exception
		// (the caller's `await` never resolves, locks may be held,
		// state may be inconsistent). Count it toward the breaker.
		if (bumpCount()) {
			console.error(
				`[VT] ${uncaughtCount} uncaught errors (rejection) — exiting to avoid zombie state`,
			);
			try {
				dialog.showErrorBox(
					mainT("dialog.criticalError.title"),
					mainT("dialog.criticalError.body", {
						count: uncaughtCount,
						logPath: rejectionLogPath,
					}),
				);
			} catch {
				// dialog may not be available in headless mode
			}
			exit(1);
		}
	};

	process.on("uncaughtException", onUncaught);
	process.on("unhandledRejection", onRejection);

	return {
		dispose: () => {
			process.off("uncaughtException", onUncaught);
			process.off("unhandledRejection", onRejection);
		},
	};
}

function setupErrorHandlers(): void {
	const userDataDir = app?.getPath("userData") ?? process.cwd();
	_installErrorHandlers({ userDataDir });
}

/**
 * Run all the one-shot runtime setup steps. Called once from
 * `app.whenReady()` in `index.ts`.
 */
export function bootstrapRuntime(): void {
	generateSessionNonce();
	setupUserData();
	setupCsp();
	setupErrorHandlers();
}
