/**
 * Runtime setup executed inside `app.whenReady()`.
 *
 * Extracted from `index.ts` (REF-2). `bootstrapRuntime()` performs:
 *   1. SEC-029 per-session nonce generation (stored in `state.sessionNonce`).
 *   2.  userData override so Electron and Python share one
 *      data root (Chromium profile tucked in ``electron-profile/``).
 *   3. SEC-012 /  Content-Security-Policy headers (HTTP).
 *   4. SEC-021 uncaughtException / unhandledRejection handlers with a
 *      crash log + 5-error circuit breaker (: log rotation +
 *      REVIEW-12 alignment + REVIEW-9 sliding window).
 *
 * : the breaker's `exit` hook now (a) calls `stopPython()` +
 * `clearElectronPidFile()` BEFORE exiting so the Python backend doesn't
 * get orphaned with a held single-instance lock + listening port, and
 * (b) schedules `app.quit()` first (giving Electron's `before-quit` /
 * `will-quit` hooks a chance to fire) with a 2s `process.exit(1)`
 * backstop in case `before-quit` hangs.
 *
 *  (R6-F7): same rationale applied to the inline `stopPython()`
 * defensive call inside `onUncaught` / `onRejection` — even when a test
 * injects an `exit` mock that bypasses `_productionExit`, the Python
 * backend is still cleaned up before the breaker trips.
 */

//prefer the static ``node:crypto`` import over the prior
// defensive dynamic ``require("node:crypto")`` — ``node:crypto`` is a
// guaranteed-built-in module (built into Node since v0.1.92), so the
// dynamic require added ~0 safety at the cost of one extra require
// resolution per ``generateSessionNonce()`` call. The static import
// also lets the bundler tree-shake unused exports.
//
//``randomUUID`` is used both for the SEC-029 session
// nonce AND for the 8-char ``VOICE_TYPER_SESSION_ID`` env var that
// the Rust host / Python sidecar / Electron main process share for
// cross-process log correlation.
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { app, crashReporter, dialog, session } from "electron";
import { PROCESS_EXIT_BACKSTOP_MS } from "./constants";
import { mainT } from "./i18n";
import { DEFAULT_CRASH_LOG_MAX_BYTES, log, rotateIfNeeded } from "./logging";
import { stopPython } from "./python";
import { clearElectronPidFile, computeConfigDir } from "./single_instance";
import { state } from "./state";

/**
 * SEC-029: generate a per-session nonce. Use crypto.randomUUID()
 * when available (Node 14.17+/Electron 12+), fall back to a
 * timestamp+random string. Stored in `state.sessionNonce` and tagged
 * onto every python-event so the renderer can reject replayed frames.
 *
 * : also derives the per-process `VOICE_TYPER_SESSION_ID`
 * (8-char lowercase-hex) used by the cross-process log-correlation
 * bracket. If the env var is already set (e.g. by a parent process
 * like a test harness), the existing value is preserved — otherwise
 * a fresh ID is minted via `crypto.randomUUID()` truncated to 8 hex
 * chars (mirrors the Rust host's `generate_or_load_session_id` and
 * the Python sidecar's `uuid.uuid4().hex[:8]`). The Python sidecar
 * (spawned via `python/index.ts`) inherits the env var via Node's
 * default `child_process` env propagation, so its file log carries
 * the SAME `[session_id]` bracket — operators can grep a single
 * bracket across Rust / Python / Electron log files.
 */
function generateSessionNonce(): void {
	try {
		//``randomUUID`` is a top-level binding imported from
		// ``node:crypto`` (see the import block above) — no dynamic
		// require needed.
		//
		// ``randomUUID`` is available on Node 14.17+ / Electron 12+
		// (both well below our minimum supported versions — see
		// ``package.json``'s ``engines.node`` field).
		const uuid = randomUUID();
		state.sessionNonce = uuid;
		//derive the 8-char session ID from the UUID's
		// first 8 hex chars (the ``uuid`` is already lowercase-hex
		// with dashes; strip dashes and take the first 8). This
		// matches the shape minted by the Rust host's
		// ``generate_or_load_session_id`` and the Python sidecar's
		// ``uuid.uuid4().hex[:8]``.
		if (!process.env.VOICE_TYPER_SESSION_ID) {
			process.env.VOICE_TYPER_SESSION_ID = uuid.replace(/-/g, "").slice(0, 8);
		}
	} catch {
		state.sessionNonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
		//best-effort fallback — if ``randomUUID``
		// threw (truly broken Crypto module), mint a less-random
		// 8-char ID from ``Date.now()`` + ``Math.random`` so the
		// bracket is still present for cross-process correlation.
		if (!process.env.VOICE_TYPER_SESSION_ID) {
			process.env.VOICE_TYPER_SESSION_ID =
				`${Date.now().toString(16).slice(-8)}${Math.random()
					.toString(16)
					.slice(2, 6)}`.slice(0, 8);
		}
	}
}

/**
 * Dedupes the ``[MAIN] userData set to: ...`` lifecycle line across
 * `setupUserData()`'s two intentional call sites (`index.ts` module-load
 * + `bootstrapRuntime()` inside `app.whenReady()`). Both calls re-set the
 * SAME path, so the line must appear only once per process — otherwise
 * `electron-stdout.log` shows a duplicate pair on every boot. Module-level
 * state is safe here: tests use `vi.resetModules()` so each test gets a
 * fresh module instance.
 */
let _loggedUserDataPath: string | undefined;

/**
 * : unify Electron's userData directory with the Python
 * backend's config directory, in a dedicated ``electron-profile``
 * subfolder.  Previously these were two separate directories:
 *   - Python: ~/.voice-typer (legacy) or platform-appropriate path
 *     (see voice_typer/server/config.py:_config_dir())
 *   - Electron: app.getPath('userData') which defaults to
 *     %APPDATA%/voice-typer-desktop (based on package.json "name")
 *
 * This caused user confusion ("where is my data?") and made GDPR
 * right-to-portability harder (two locations to scrub).  We now
 * explicitly set Electron's userData to the Python config dir's
 * ``electron-profile`` subfolder so both sides share one data root
 * (uninstall / factory-reset still wipes everything) while the
 * Chromium browser-profile noise stays out of the data-dir root.
 */
export function setupUserData(): void {
	try {
		const configDir = computeConfigDir();
		// Electron's Chromium profile (caches, Local Storage, Network
		// state, Crashpad, ...) lives in a subfolder so the data-dir
		// root stays a readable mix of user data + app logs.
		const electronProfileDir = path.join(configDir, "electron-profile");
		// Ensure the directory exists before Electron tries to use it.
		try {
			fs.mkdirSync(configDir, { recursive: true });
		} catch (e) {
			// Previously this catch was silent (`/* ignore */`),
			// which masked three distinct failure modes:
			//   1. Permissions error on a shared / multi-user install
			//      (e.g. /opt owned by root).
			//   2. Disk full / read-only filesystem (Live USB).
			//   3. Path-too-long on Windows (MAX_PATH=260).
			// All three surfaced downstream as a cryptic
			// `app.setPath("userData", ...)` failure with no
			// upstream context. Logging here gives operators a
			// breadcrumb pointing at the real cause. The mkdir
			// is still best-effort — Electron falls back to its
			// default userData if `app.setPath` is never called.
			log.warn("[MAIN] mkdirSync for userData failed:", e);
		}
		app.setPath("userData", electronProfileDir);
		//route through the structured `log` logger so
		// the lifecycle message persists to `electron-runtime.log` instead
		// of being lost in packaged builds where `console.warn` has no
		// terminal attached. Log once per process — the second call
		// (idempotent re-set of the same path from `bootstrapRuntime()`)
		// must not duplicate the line.
		if (_loggedUserDataPath !== electronProfileDir) {
			_loggedUserDataPath = electronProfileDir;
			log.info(`[MAIN] userData set to: ${electronProfileDir}`);
		}
	} catch (e) {
		log.warn("[MAIN] Failed to override userData path:", e);
		// Non-fatal — Electron falls back to its default userData location.
	}
}

/**
 * Build the Content-Security-Policy header string.
 *
 * Exported as `_buildCsp` (test seam) so `bootstrap-csp.test.ts` can pin
 * the C-DATA-1 offline guarantee without having to drive the full
 * `setupCsp()` → `session.defaultSession.webRequest.onHeadersReceived`
 * wiring. `setupCsp()` is a thin wrapper that calls `_buildCsp` and
 * installs the result as an HTTP header via Electron's webRequest API.
 *
 * @param opts.isPackaged - mirrors `app.isPackaged`. In dev mode
 *   (`isPackaged === false`) Vite's dev server injects inline scripts
 *   (React Refresh preamble + HMR client) and uses eval for sourcemaps,
 *   so 'unsafe-inline' + 'unsafe-eval' are added to `script-src`. In
 *   production the strict 'self'-only directive applies.
 * @returns the CSP string, directives joined by `"; "`.
 */
export function _buildCsp(opts: { isPackaged: boolean }): string {
	return [
		"default-src 'self'",
		`script-src 'self'${opts.isPackaged === false ? " 'unsafe-eval' 'unsafe-inline'" : ""}`,
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data:",
		"font-src 'self' data:",
		"media-src 'self' data:",
		// C-DATA-1: connect-src restricted to 'self' — no external network calls. Cloud-test/check-update calls must route through the Python sidecar.
		"connect-src 'self'",
		"frame-ancestors 'none'",
		"form-action 'none'",
		"base-uri 'self'",
	].join("; ");
}

/**
 * SEC-012 / : Content Security Policy (HTTP headers).
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
	const CSP = _buildCsp({ isPackaged: app.isPackaged });

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
 *  (IMPL-7): the crash log used to grow unbounded — `appendFileSync`
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
 * : the production exit hook (passed in by `setupErrorHandlers`
 * below) now calls `stopPython()` + `clearElectronPidFile()` BEFORE
 * `app.exit(1)`, then schedules a 2s `process.exit(1)` backstop. This
 * closes the orphan-Python bug where `process.exit(1)` (the old
 * implementation) bypassed Electron's `before-quit` → `stopPython()`
 * never ran → Python kept its IPC port + single-instance mutex +
 * tray icon alive. The 2s backstop guarantees we still exit even if
 * `app.quit()` hangs (e.g. a stuck `before-quit` handler).
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
			//production exit path. The injected `exit`
			// hook below (in `setupErrorHandlers`) replaces
			// this default with the full `stopPython` +
			// `clearElectronPidFile` + `app.quit` + 2s
			// `process.exit` backstop sequence. Tests inject
			// a plain `(code) => exitCalls.push(code)` mock.
			app.exit(code);
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
		} catch (e) {
			//surface the failure.
			log.error("[bootstrap] logEvent failed for", filePath, e);
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

	/**
	 * : shared trip-breaker logic for `uncaughtException` and
	 * `unhandledRejection`. Both event types share the same counter
	 * (REVIEW-12 alignment — a rejected promise leaves the app in
	 * the same half-broken state as an uncaught exception: the
	 * caller's `await` never resolves, locks may be held, state may
	 * be inconsistent) and the same exit-cleanup sequence
	 * ( + ). Only the log file path + the kind
	 * label differ — those are passed in so the helper can route
	 * the log line + dialog message correctly.
	 *
	 * Behaviour (mirrors the original `onUncaught` / `onRejection`):
	 *   1. `console.error("[VT] <kind>:", err)` — surface on stderr.
	 *   2. `logEvent(logPath, kind, err)` — append to the per-kind
	 *      log file (: rotates independently).
	 *   3. `bumpCount()` — increment + sliding-window reset
	 *      (REVIEW-9). On trip (`>= MAX_UNCAUGHT`):
	 *      a. `console.error` the trip message (with " (rejection)"
	 *         suffix for `unhandledRejection` to preserve the
	 *         original wording).
	 *      b. `dialog.showErrorBox(...)` with the kind's log path.
	 *      c. `stopPython()` + `clearElectronPidFile()` — inline
	 *         defensive cleanup so the breaker doesn't orphan the
	 *         Python backend (microphone, global hotkeys, volume
	 *         duck, single-instance mutex). Best-effort — these are
	 *         wrapped in try/catch internally, but we double-guard
	 *         here so a throw in either cannot block the exit. The
	 *         same defensive cleanup applies if a test injects an
	 *         `exit` mock that bypasses `_productionExit`.
	 *      d. `exit(1)`.
	 */
	const tripBreaker = (
		logPath: string,
		kind: "uncaughtException" | "unhandledRejection",
		err: unknown,
	): void => {
		const suffix = kind === "unhandledRejection" ? " (rejection)" : "";
		//route through the structured `log` logger so the
		// uncaught/rejected error is captured in `electron-runtime.log` (with
		// 5 MiB rotation) for post-mortem analysis — `console.error` alone is
		// lost in packaged GUI builds where stderr is attached to a hidden
		// console / dev/null. `log.error`'s stdout tee internally calls
		// `console.error`, so stderr is still captured by Electron's crash
		// reporter.
		log.error(`[VT] ${kind}:`, err);
		logEvent(logPath, kind, err);
		if (bumpCount()) {
			log.error(
				`[VT] ${uncaughtCount} uncaught errors${suffix} — exiting to avoid zombie state`,
			);
			try {
				dialog.showErrorBox(
					mainT("dialog.criticalError.title"),
					mainT("dialog.criticalError.body", {
						count: uncaughtCount,
						logPath,
					}),
				);
			} catch {
				// dialog may not be available in headless mode
			}
			try {
				stopPython();
			} catch (e) {
				log.error(`[VT] stopPython() failed during breaker exit${suffix}:`, e);
			}
			try {
				clearElectronPidFile();
			} catch (e) {
				log.error(
					`[VT] clearElectronPidFile() failed during breaker exit${suffix}:`,
					e,
				);
			}
			exit(1);
		}
	};

	const onUncaught = (err: unknown) =>
		tripBreaker(crashLogPath, "uncaughtException", err);
	const onRejection = (err: unknown) =>
		tripBreaker(rejectionLogPath, "unhandledRejection", err);

	process.on("uncaughtException", onUncaught);
	process.on("unhandledRejection", onRejection);

	return {
		dispose: () => {
			process.off("uncaughtException", onUncaught);
			process.off("unhandledRejection", onRejection);
		},
	};
}

/**
 * : production exit hook for the SEC-021 circuit breaker.
 *
 * Replaces the previous `process.exit(1)` (which bypassed Electron's
 * `before-quit` lifecycle — `stopPython()` and `clearElectronPidFile()`
 * never ran, orphaning the Python backend with its IPC port + single-
 * instance mutex + tray icon).
 *
 * Sequence:
 *   1. `stopPython()` — sends `quit_app` over TCP, force-kills after 3s
 *      via the SIGTERM→SIGKILL escalation in `python/stop-python.ts`.
 *   2. `clearElectronPidFile()` — removes `electron.pid` so the next
 *      launch doesn't think we're still alive.
 *   3. `app.quit()` — fires `before-quit` → `will-quit` (gives the
 *      Python-side shutdown ack a chance to land + lets any other
 *      `will-quit` listeners run).
 *   4. 2s `process.exit(1)` backstop — if `app.quit()` hangs (a stuck
 *      `before-quit` handler, a deadlock in the Python IPC ack path),
 *      we still exit so the user isn't left with a zombie process.
 *
 * The prior synchronous `state.pythonProcess?.kill("SIGKILL")`
 * step between (2) and (3) was removed. It defeated the graceful
 * `stopPython()` shutdown in step (1): the SIGTERM→SIGKILL escalation
 * inside `stopPython()` already covers the "Python won't exit" case
 * within its own 3s+3s schedule, and the `PROCESS_EXIT_BACKSTOP_MS`
 * `process.exit(1)` in step (4) covers the "Electron won't exit" case.
 * The synchronous SIGKILL fired UNCONDITIONALLY — even when Python had
 * already exited cleanly from the `quit_app` IPC — which (a) races the
 * already-exited pid (the kernel may have recycled it for an unrelated
 * process — `kill(SIGKILL)` on a recycled pid is a security-relevant
 * footgun), and (b) skips Python's atexit hooks (`tray.py::_atexit`,
 * `single_instance` lock release), leaving stale locks on disk.
 *
 * The hook is idempotent: if `app.quit()` succeeds and the process
 * exits before 2s, the `setTimeout` callback never fires (Node exits
 * the event loop). If `app.quit()` is a no-op (already quitting), the
 * backstop still fires.
 */
function _productionExit(code: number): void {
	try {
		stopPython();
	} catch (e) {
		log.error("[VT] stopPython() failed during production exit:", e);
	}
	try {
		clearElectronPidFile();
	} catch (e) {
		log.error("[VT] clearElectronPidFile() failed during production exit:", e);
	}
	// Schedule the quit call so Electron's before-quit/will-quit hooks fire.
	try {
		app.quit();
	} catch (e) {
		log.error("[VT] app.quit() failed during production exit:", e);
	}
	// Production-exit backstop: if `app.quit()` doesn't actually exit the
	// process within `PROCESS_EXIT_BACKSTOP_MS` (e.g. a `before-quit`
	// handler called `event.preventDefault()` or the Python shutdown ack
	// hangs), force-exit so the user isn't left with a zombie.
	setTimeout(() => {
		process.exit(code);
	}, PROCESS_EXIT_BACKSTOP_MS).unref();
}

//stores the dispose handle from the last `setupErrorHandlers` call so a
// subsequent call can dispose old listeners before stacking new ones.
let _errorHandlersDispose: (() => void) | undefined;

export function _resetErrorHandlersDisposeForTest(): void {
	_errorHandlersDispose = undefined;
}

export function setupErrorHandlers(): void {
	//dispose previously installed handlers before adding new ones
	// so repeated calls (e.g. in tests) don't accumulate listeners.
	if (_errorHandlersDispose) {
		_errorHandlersDispose();
	}
	const userDataDir = app?.getPath("userData") ?? process.cwd();
	_errorHandlersDispose = _installErrorHandlers({
		userDataDir,
		exit: _productionExit,
	}).dispose;
}

// Idempotency guard for the `app.on("child-process-gone", ...)` handler.
// Without this, a second `bootstrapRuntime()` call (tests via
// `vi.resetModules()`, or a future defensive double-call site) would
// stack a fresh listener, double-logging every GPU / utility-process
// crash.
let _childProcessGoneHandlerRegistered = false;

export function _resetChildProcessGoneHandlerForTest(): void {
	_childProcessGoneHandlerRegistered = false;
}

export function _childProcessGoneHandlerRegisteredForTest(): boolean {
	return _childProcessGoneHandlerRegistered;
}

/**
 * Set the Windows App User Model ID so taskbar grouping works correctly.
 * Best-effort — only matters on Windows 7+. Called from `bootstrapRuntime()`
 * between `setupUserData()` and `setupCsp()` so it runs inside
 * `app.whenReady()` rather than at module-load time (defers the Windows
 * registry write out of the module-evaluation hot path).
 */
function setupAppUserModelId(): void {
	try {
		app.setAppUserModelId("VoiceTyper");
	} catch (e) {
		// setAppUserModelId can throw on non-Windows or if the
		// registry write fails; non-fatal — Windows taskbar grouping
		// falls back to the default (app.exe name) which is acceptable.
		log.warn("[bootstrap] setAppUserModelId failed (non-fatal):", e);
	}
}

/**
 * Run all the one-shot runtime setup steps. Called once from
 * `app.whenReady()` in `index.ts`.
 */
export function bootstrapRuntime(): void {
	generateSessionNonce();
	setupUserData();
	setupAppUserModelId();
	setupCsp();
	setupErrorHandlers();
	//best-effort crash reporter.
	try {
		crashReporter.start({ uploadToServer: false });
	} catch (e) {
		log.warn("[bootstrap] crashReporter.start failed (non-fatal):", e);
	}
	//surface child/GPU process crashes. Idempotency guard —
	// `bootstrapRuntime()` may be invoked more than once (tests via
	// `vi.resetModules()`, or a future defensive double-call site).
	// Without the guard, each call would stack a fresh
	// `app.on("child-process-gone", ...)` listener, double-logging
	// every GPU / utility-process crash.
	if (!_childProcessGoneHandlerRegistered) {
		_childProcessGoneHandlerRegistered = true;
		try {
			app.on("child-process-gone", (_e: unknown, details: unknown) => {
				log.error("child-process-gone", details);
			});
		} catch (e) {
			log.warn("[bootstrap] child-process-gone handler failed:", e);
		}
	}
}
