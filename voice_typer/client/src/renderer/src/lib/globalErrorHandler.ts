/**
 * ERR-5: global error + unhandledrejection handlers for the renderer.
 *
 * Before this module existed, the renderer had NO global listener for
 * ``window.onerror`` or ``unhandledrejection`` events. Errors that
 * escaped React's render cycle (e.g. an async ``useEffect`` fetch
 * that rejected without a ``.catch()``) were silently swallowed —
 * the user saw no feedback, and the only trace was a dev-tools
 * console message that disappeared on refresh.
 *
 * ``installGlobalErrorHandlers()`` registers two listeners:
 *
 *   1. ``window.addEventListener("error", ...)`` — catches synchronous
 *      errors (e.g. ``throw`` in an event handler outside React's
 *      boundary, syntax errors in dynamically-imported modules).
 *   2. ``window.addEventListener("unhandledrejection", ...)`` —
 *      catches Promise rejections that have no ``.catch()`` handler
 *      (the most common source of silent renderer failures).
 *
 * Both listeners:
 *   - log to ``console.error`` with a ``[Renderer]`` prefix so the
 *     message is visible in the Electron main-process console
 *     (forwarded via ``webContents.on("console-message")``) and in
 *     DevTools;
 *   - show a generic localized toast via ``sonner.toast.error`` so
 *     the user gets immediate visual feedback that something went
 *     wrong. The toast message is intentionally generic (no error
 *     details leaked to the UI — the full stack is in the console
 *     for the developer/operator to diagnose).
 *
 * The handlers are idempotent — calling ``installGlobalErrorHandlers``
 * twice is safe (the second call is a no-op).
 *
 * Integration: ``main.tsx`` calls ``installGlobalErrorHandlers()``
 * BEFORE ``ReactDOM.createRoot().render()`` so the listeners are in
 * place before any React render or effect runs. This catches errors
 * in module-level code (e.g. a top-level ``await`` in an imported
 * module) that fire before React mounts.
 */

import { toast } from "sonner";

// CR-059: hoist the i18n import to module scope. The previous
// implementation used ``require("../i18n/i18n")`` inside the
// ``_genericUserMessage`` helper, but ``require`` is undefined in
// Electron renderer processes under ``contextIsolation: true`` +
// ``nodeIntegration: false`` — the call always threw and the catch
// block silently fell back to the hardcoded English string. Importing
// ``t`` as a top-level ESM binding is the renderer-safe equivalent:
// the bundler (Vite) resolves the import at build time, the function
// is a pure lookup (no side effects), and ``t()`` itself falls back
// to English when the key is missing from the active locale map.
import { t } from "@/i18n/i18n";

let _installed = false;

/**
 * Generic, localized error message for the user-facing toast.
 *
 * The full error details (message + stack) are logged to the console
 * for developers; the toast intentionally hides them to avoid leaking
 * implementation details (file paths, internal module names) that
 * could confuse users or, in a worst case, aid an attacker probing
 * the renderer surface.
 *
 * CR-059: previously called ``require("../i18n/i18n")`` lazily so
 * the global error handler could be installed before the i18n module
 * loaded. That reasoning was sound but ``require`` is not available
 * in the sandboxed renderer — so the lazy import always failed and
 * the hardcoded English fallback always won. With the top-level ESM
 * ``import`` we now actually resolve the localized string.
 */
function _genericUserMessage(): string {
	// ``t()`` is a pure lookup that falls back to English, then to
	// the raw key. It cannot throw — but we still guard so a future
	// i18n implementation that does throw never breaks the global
	// error handler.
	try {
		const msg = t("errorBoundary.description");
		if (typeof msg === "string" && msg.length > 0) return msg;
	} catch {
		// Fall through to the hardcoded default.
	}
	return "The app encountered an unexpected error. Your data is safe.";
}

/**
 * Format an error-like value for the console log.
 *
 * Accepts:
 *   - ``Error`` instances (use ``.message`` + ``.stack``)
 *   - ``string`` (use as-is)
 *   - ``{ message: string, filename?: string, lineno?: number, colno?: number }``
 *     (the DOM ``ErrorEvent`` shape)
 *   - anything else (coerce to string via ``String(value)``)
 */
function _formatForConsole(err: unknown): string {
	if (err instanceof Error) {
		return err.stack || `${err.name}: ${err.message}`;
	}
	if (typeof err === "string") return err;
	if (err && typeof err === "object") {
		const e = err as {
			message?: unknown;
			filename?: unknown;
			lineno?: unknown;
			colno?: unknown;
			stack?: unknown;
		};
		const msg = typeof e.message === "string" ? e.message : String(err);
		const loc =
			typeof e.filename === "string" && typeof e.lineno === "number"
				? `\n  at ${e.filename}:${e.lineno}${
						typeof e.colno === "number" ? `:${e.colno}` : ""
					}`
				: "";
		const stack = typeof e.stack === "string" ? `\n${e.stack}` : "";
		return `${msg}${loc}${stack}`;
	}
	return String(err);
}

/**
 * Format a Promise rejection reason for the console log.
 *
 * Same heuristic as ``_formatForConsole`` but with a distinct prefix
 * so operators can tell unhandled rejections apart from synchronous
 * errors in the log.
 */
function _formatReasonForConsole(reason: unknown): string {
	return _formatForConsole(reason);
}

/**
 * Install the global ``error`` and ``unhandledrejection`` listeners.
 *
 * Idempotent: safe to call multiple times. The second call is a
 * no-op (the listeners are registered at most once).
 *
 * Call this BEFORE ``ReactDOM.createRoot().render()`` in ``main.tsx``
 * so the listeners are in place before any React render or effect
 * runs. Errors in module-level code (top-level ``await``, dynamic
 * import failures) are then caught.
 */
export function installGlobalErrorHandlers(): void {
	if (_installed) return;
	_installed = true;

	if (
		typeof window === "undefined" ||
		typeof window.addEventListener !== "function"
	) {
		// Not a browser environment (e.g. Node SSR or a test runner
		// without a real DOM). Skip — the renderer always runs in a
		// real browser (Electron Chromium), so this is defensive.
		return;
	}

	// Synchronous errors (script parse errors, throws in event handlers
	// outside React's boundary, etc.).
	window.addEventListener("error", (event: ErrorEvent) => {
		const detail = _formatForConsole(event.error ?? event.message);
		console.error("[Renderer] uncaught error:", detail);
		try {
			toast.error(_genericUserMessage());
		} catch {
			// If sonner isn't mounted yet (e.g. error during bootstrap
			// before the Toaster component renders), the toast call is a
			// no-op. The console.error above still surfaces the error.
		}
		// Do NOT call event.preventDefault() — we want the default
		// browser console error to also appear in DevTools for parity
		// with the pre-listener behavior.
	});

	// Promise rejections with no ``.catch()`` handler.
	window.addEventListener(
		"unhandledrejection",
		(event: PromiseRejectionEvent) => {
			const detail = _formatReasonForConsole(event.reason);
			console.error("[Renderer] unhandled promise rejection:", detail);
			try {
				toast.error(_genericUserMessage());
			} catch {
				// Same defensive guard as above.
			}
			// Do NOT call event.preventDefault() — let the default browser
			// warning appear in DevTools too.
		},
	);
}

/**
 * Test-only: reset the "installed" flag so unit tests can re-install
 * the handlers in a clean state.
 *
 * Not part of the public API; exported only for test isolation.
 */
export function _resetGlobalErrorHandlerStateForTests(): void {
	_installed = false;
}
