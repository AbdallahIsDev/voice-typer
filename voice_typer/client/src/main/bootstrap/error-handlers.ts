/**
 * SEC-021 uncaughtException / unhandledRejection handlers with a crash
 * log + 5-error circuit breaker.
 *
 * Split out of `bootstrap.ts` (step 4 of the bootstrap sequence).
 */
import fs from "node:fs";
import path from "node:path";
import { app, dialog } from "electron";
import { PROCESS_EXIT_BACKSTOP_MS } from "../constants";
import { mainT } from "../i18n";
import {
	DEFAULT_CRASH_LOG_MAX_BYTES,
	fileTimestamp,
	log,
	rotateIfNeeded,
} from "../logging";
import { stopPython } from "../python";
import { clearElectronPidFile, computeConfigDir } from "../single_instance";

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
 *
 * O1: crash logs live under `<config-dir>/logs/` (the canonical log
 * folder) — NOT the Electron userData profile dir.  The `logsDir`
 * parameter is the folder the caller resolves (production:
 * `computeConfigDir()/logs`).
 */
export function _crashLogPaths(logsDir: string): {
	crashLogPath: string;
	rejectionLogPath: string;
} {
	return {
		crashLogPath: path.join(logsDir, "electron-crashes.log"),
		rejectionLogPath: path.join(logsDir, "electron-rejections.log"),
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
			const ts = fileTimestamp();
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
	// O1: crash logs live under `<config-dir>/logs/` — the canonical
	// log folder shared with the structured/printf loggers and the
	// Python backend.  The Electron userData dir (electron-profile) is
	// NOT a log location.
	const logsDir = path.join(computeConfigDir(), "logs");
	_errorHandlersDispose = _installErrorHandlers({
		userDataDir: logsDir,
		exit: _productionExit,
	}).dispose;
}
