/**
 * Runtime setup executed inside `app.whenReady()` — public facade.
 *
 * Extracted from `index.ts` (REF-2); the implementation now lives in
 * focused leaves under `bootstrap/` and is re-exported here so every
 * existing import site (and source-text test pin) keeps resolving:
 *   - `session-identity.ts` — SEC-029 per-session nonce + the
 *     `VOICE_TYPER_SESSION_ID` cross-process log-correlation env var.
 *   - `user-data.ts` — userData override so Electron and Python share
 *      one data root (Chromium profile tucked in ``electron-profile/``).
 *   - `csp.ts` — SEC-012 /  Content-Security-Policy headers (HTTP).
 *   - `error-handlers.ts` — SEC-021 uncaughtException /
 *     unhandledRejection handlers with a crash log + 5-error circuit
 *     breaker (: log rotation + REVIEW-12 alignment + REVIEW-9 sliding
 *     window).
 *   - `runtime.ts` — the `bootstrapRuntime()` orchestrator.
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

export { _buildCsp } from "./bootstrap/csp";
export {
	_crashLogPaths,
	_installErrorHandlers,
	_resetErrorHandlersDisposeForTest,
	setupErrorHandlers,
} from "./bootstrap/error-handlers";
export {
	_childProcessGoneHandlerRegisteredForTest,
	_resetChildProcessGoneHandlerForTest,
	bootstrapRuntime,
} from "./bootstrap/runtime";
export { setupUserData } from "./bootstrap/user-data";
