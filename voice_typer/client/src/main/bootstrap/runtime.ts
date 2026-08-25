/**
 * `bootstrapRuntime()` orchestrator.
 *
 * Split out of `bootstrap.ts`. Runs the one-shot runtime setup steps in
 * order (session nonce → userData → App User Model ID → CSP → error
 * handlers → crash reporter → child-process-gone guard) from
 * `app.whenReady()` in `index.ts`.
 */
import { app, crashReporter } from "electron";
import { log } from "../logging";
import { setupCsp } from "./csp";
import { setupErrorHandlers } from "./error-handlers";
import { generateSessionNonce } from "./session-identity";
import { setupUserData } from "./user-data";

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
