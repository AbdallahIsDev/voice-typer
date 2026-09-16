/**
 * : global error + unhandledrejection handlers for the renderer.
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
 *   1. ``window.addEventListener("error", ...)``, catches synchronous
 *      errors (e.g. ``throw`` in an event handler outside React's
 *      boundary, syntax errors in dynamically-imported modules).
 *   2. ``window.addEventListener("unhandledrejection", ...)`` —
 *      catches Promise rejections that have no ``.catch()`` handler
 *      (the most common source of silent renderer failures).
 *
 * Both listeners:
 *   - log to ``console.error`` with a ``[renderer:globalErrorHandler]`` prefix so the
 *     message is visible in the Electron main-process console
 *     (forwarded via ``webContents.on("console-message")``) and in
 *     DevTools;
 *   - show a generic localized toast via ``sonner.toast.error`` so
 *     the user gets immediate visual feedback that something went
 *     wrong. The toast message is intentionally generic (no error
 *     details leaked to the UI, the full stack is in the console
 *     for the developer/operator to diagnose).
 *
 * : the toast now uses a STABLE id (``'global-error-handler'``)
 * so successive errors REPLACE the existing toast instead of stacking
 * on top of each other. Previously a tight error loop (e.g. an effect
 * that re-threw on every retry) could pile up dozens of identical
 * toasts, making the UI unreadable. With the stable id, sonner
 * dedupes, only the most recent error's toast stays visible.
 *
 * : the toast now exposes two action buttons:
 *   • ``View logs``, calls ``window.window_?.openLogs?.()`` to open
 *     the Python backend's log folder in the OS file manager (the
 *     full stack trace + IPC error details live there for diagnosis).
 *   • ``Copy error``, writes the most recent error's formatted
 *     stack to the clipboard so users can paste it into a bug report.
 *     The formatted string is passed directly into the toast options
 *     builder (no module-level state, each error event carries its
 *     own detail into the action-button closure).
 *
 * The handlers are idempotent, calling ``installGlobalErrorHandlers``
 * twice is safe (the second call is a no-op).
 *
 * Integration: ``main.tsx`` calls ``installGlobalErrorHandlers()``
 * BEFORE ``ReactDOM.createRoot().render()`` so the listeners are in
 * place before any React render or effect runs. This catches errors
 * in module-level code (e.g. a top-level ``await`` in an imported
 * module) that fire before React mounts.
 */

import { toast } from "sonner";

//hoist the i18n import to module scope. The previous
// implementation used ``require("../i18n/i18n")`` inside the
// ``_genericUserMessage`` helper, but ``require`` is undefined in
// Electron renderer processes under ``contextIsolation: true`` +
// ``nodeIntegration: false``, the call always threw and the catch
// block silently fell back to the hardcoded English string. Importing
// ``t`` as a top-level ESM binding is the renderer-safe equivalent:
// the bundler (Vite) resolves the import at build time, the function
// is a pure lookup (no side effects), and ``t()`` itself falls back
// to English when the key is missing from the active locale map.
import { t } from "@/i18n/i18n";

let _installed = false;

/** The live listener pair, so the test-only reset can REMOVE them.
 * Production installs once and keeps them for the process lifetime. */
let _installedHandlers: {
	onError: (event: ErrorEvent) => void;
	onUnhandledRejection: (event: PromiseRejectionEvent) => void;
} | null = null;

//stable toast id so successive errors replace (not stack on
// top of) the existing toast. Sonner's ``id`` option dedupes, the
// second ``toast.error(msg, {id})`` call updates the existing toast
// in place rather than spawning a second one.
const GLOBAL_ERROR_TOAST_ID = "global-error-handler";

/**
 * Source position of a window `error` event, or `undefined`.
 *
 * `ErrorEvent.filename` / `.lineno` / `.colno` are ADJACENT properties
 * on the event (they are not nested under a `location` key and are NOT
 * part of the `Error` interface, so `event.error` never carries them).
 * Narrowed with real `typeof` guards: no assertion, and a plain object
 * or string source simply yields `undefined`.
 */
function _rendererErrorLocation(
	source: unknown,
): { file: string; line?: number; column?: number } | undefined {
	if (source === null || typeof source !== "object") return undefined;
	const raw = source as Record<string, unknown>;
	if (typeof raw.filename !== "string" || raw.filename.length === 0) {
		return undefined;
	}
	return {
		file: raw.filename,
		line: typeof raw.lineno === "number" ? raw.lineno : undefined,
		column: typeof raw.colno === "number" ? raw.colno : undefined,
	};
}

/**
 * Build the `renderer_log_error` payload for a crash (MO-102).
 *
 * Pure, so the wire shape is unit-testable: `kind` + `message` +
 * optional `stack` and `location`. The Rust command renders these into
 * ONE canonical C-LOG-1 line
 * (`[renderer-error] <message> (src=<file>:<line>:<col>) scope=<kind>`).
 *
 * `source` is the originating `ErrorEvent` (for the source position);
 * `detail` is what was thrown/rejected.
 */
function _buildLogErrorPayload(
	kind: string,
	detail: unknown,
	source?: unknown,
): {
	kind: string;
	message: string;
	stack?: string;
	location?: { file: string; line?: number; column?: number };
} {
	const thrown = detail instanceof Error ? detail : undefined;
	const asRecord =
		detail !== null && typeof detail === "object"
			? (detail as Record<string, unknown>)
			: undefined;
	const message =
		typeof thrown?.message === "string"
			? thrown.message
			: typeof asRecord?.message === "string"
				? asRecord.message
				: typeof detail === "string"
					? detail
					: String(detail);
	const stack =
		typeof thrown?.stack === "string"
			? thrown.stack
			: typeof asRecord?.stack === "string"
				? asRecord.stack
				: undefined;
	return {
		kind,
		message,
		stack,
		location: _rendererErrorLocation(source),
	};
}

/**
 * Persist a generic renderer crash to the host log (MO-102).
 *
 * Under Electron the `console.error` calls below are enough — the main
 * process tees console output into ``electron-runtime.log``. Under
 * Tauri there is NO console capture (the bridge does dispatch/listen
 * only), so a crash that fires outside React's boundary previously left
 * zero file trace in a release install (no DevTools). This forwards the
 * error through ``window.window_.logError`` (the same sink React's
 * ``ErrorBoundary`` uses), best-effort: the promise is swallowed so a
 * failing persistence command can never add a SECOND error on top of
 * the one being handled, and the whole call is guarded so a runtime
 * without the bridge (tests, older preload) is a no-op.
 *
 * `source` is the originating event (optional) and supplies the
 * `location` field for window `error` events.
 *
 * Testability: routed through ``_persistForTests`` so unit tests can
 * stub the module export.
 */
function _persistRendererError(
	kind: string,
	detail: unknown,
	source?: unknown,
): void {
	try {
		const logError = window.window_?.logError?.bind(window.window_);
		if (typeof logError !== "function") return;
		void Promise.resolve(
			logError(_buildLogErrorPayload(kind, detail, source)),
		).catch(() => {
			// Best-effort: never let the persistence path become its own
			// unhandled rejection.
		});
	} catch (e) {
		// Same contract: logging must never throw.
		console.warn("[renderer:globalErrorHandler] logError forward failed:", e);
	}
}

/** Test seam: the unit tests stub this to capture the persisted payload
 * without installing a real bridge. */
export const _persistForTests = { persist: _persistRendererError };

/** Test seam: the pure payload builder (asserted directly by tests). */
export const _payloadForTests = { build: _buildLogErrorPayload };

/**
 * Generic, localized error message for the user-facing toast.
 *
 * The full error details (message + stack) are logged to the console
 * for developers; the toast intentionally hides them to avoid leaking
 * implementation details (file paths, internal module names) that
 * could confuse users or, in a worst case, aid an attacker probing
 * the renderer surface.
 *
 * Previously this function called ``require("../i18n/i18n")`` lazily
 * so the global error handler could be installed before the i18n module
 * loaded. That reasoning was sound but ``require`` is not available
 * in the sandboxed renderer, so the lazy import always failed and
 * the hardcoded English fallback always won. With the top-level ESM
 * ``import`` we now actually resolve the localized string.
 *
 * The hardcoded English fallback
 * "The app encountered an unexpected error. Your data is safe." has
 * been dropped. ``t()`` is a pure lookup that walks the
 * currentLocale → primary-subtag → en → raw-key chain, it never
 * throws and never returns an empty string (the worst case is the
 * raw dot-path key, which is ugly but unambiguously signals broken
 * i18n to the developer). The defensive try/catch is retained so a
 * FUTURE i18n implementation that does throw can never break the
 * global error handler; in that case we return the raw key rather
 * than a hardcoded English string, preserving the "broken i18n
 * should be visible, not silently masked" invariant.
 */
function _genericUserMessage(): string {
	try {
		return t("errorBoundary.description");
	} catch (e) {
		// The i18n module should never throw, but we guard so a future
		// implementation can't break the global error handler. Return
		// the raw key (not a hardcoded English string) so broken i18n
		// is unambiguously visible to developers rather than silently
		// masked.
		console.warn("[renderer:globalErrorHandler] i18n t() failed:", e);
		return "errorBoundary.description";
	}
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
 * Safely resolve a localized string, letting the i18n layer
 * handle missing keys. The i18n layer (``i18n/translate.ts``) walks
 * the currentLocale → primary-subtag → en → raw-key chain, it never
 * throws and returns the raw dot-path key as the last-resort
 * fallback (e.g. ``t("errors.viewLogsAction")`` returns
 * ``"errors.viewLogsAction"`` when the key is missing from BOTH the
 * active locale AND the English fallback table). That raw key is
 * ugly but unambiguously signals broken i18n to the developer; it
 * is preferable to silently masking the gap with a hardcoded
 * English string that hides the missing-key bug.
 *
 * The defensive try/catch is retained so a FUTURE i18n
 * implementation that does throw can never break the global error
 * handler; in that case we return the raw key rather than a
 * hardcoded English string.
 */
export function _safeT(key: string): string {
	try {
		return t(key);
	} catch (e) {
		console.warn(`[renderer:globalErrorHandler] i18n t("${key}") failed:`, e);
		return key;
	}
}

/**
 * : build the sonner toast options for the global error toast.
 *
 * Returns an options object with:
 *   • ``id``, the stable toast id ( dedup).
 *   • ``action``, the primary action button ("View logs" →
 *     ``window.window_?.openLogs?.()``).
 *   • ``cancel``, the secondary action button ("Copy error" →
 *     copies the last formatted error stack to the clipboard via
 *     ``navigator.clipboard.writeText``).
 *
 * Both buttons are defensively guarded: ``window.window_`` may not
 * exist (older preload scripts, Tauri bridge), and
 * ``navigator.clipboard`` may not exist (non-secure context, SSR
 * snapshot). A missing affordance is silently ignored, the toast
 * still renders with whatever buttons ARE available.
 */
function _buildToastOptions(formattedError: string): {
	id: string;
	action?: { label: string; onClick: () => void };
	cancel?: { label: string; onClick: () => void };
} {
	const opts: {
		id: string;
		action?: { label: string; onClick: () => void };
		cancel?: { label: string; onClick: () => void };
	} = { id: GLOBAL_ERROR_TOAST_ID };

	// "View logs" action, opens the Python backend's log folder.
	// The bridge method is optional (older preload scripts / Tauri
	// bridge may not install it); silently skip when unavailable.
	// Read directly from the globally-augmented ``window.window_``
	// (declared in ``types/ipc/bubble_bridge.ts``) instead of
	// re-declaring the bridge shape inline.
	const windowApi = window.window_;
	if (typeof windowApi?.openLogs === "function") {
		opts.action = {
			label: _safeT("errors.viewLogsAction"),
			onClick: () => {
				try {
					void windowApi.openLogs?.();
				} catch (e) {
					console.warn("[renderer:globalErrorHandler] openLogs() threw:", e);
				}
			},
		};
	}

	// "Copy error" cancel-side action, writes the formatted stack
	// to the clipboard. ``navigator.clipboard`` may be missing in
	// non-secure contexts (older Tauri / file:// / SSR); silently
	// skip when unavailable.
	if (
		typeof navigator !== "undefined" &&
		typeof navigator.clipboard?.writeText === "function"
	) {
		opts.cancel = {
			label: _safeT("errors.copyErrorAction"),
			onClick: () => {
				navigator.clipboard
					.writeText(formattedError)
					.catch((e) =>
						console.warn(
							"[renderer:globalErrorHandler] clipboard.writeText failed:",
							e,
						),
					);
			},
		};
	}

	return opts;
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
		// without a real DOM). Skip, the renderer always runs in a
		// real browser (Electron Chromium), so this is defensive.
		return;
	}

	// Synchronous errors (script parse errors, throws in event handlers
	// outside React's boundary, etc.).
	const onError = (event: ErrorEvent) => {
		const detail = _formatForConsole(event.error ?? event.message);
		console.error("[renderer:globalErrorHandler] uncaught error:", detail);
		// MO-102: persist to the host log (no-op where the bridge is
		// absent). Fired BEFORE the toast so a toast failure can never
		// suppress the persistence path. `event` is passed as the
		// SOURCE so the payload carries the `filename:lineno:colno`
		// position (those live on the ErrorEvent, never on
		// `event.error`).
		_persistForTests.persist("error", event.error ?? event.message, event);
		try {
			toast.error(_genericUserMessage(), _buildToastOptions(detail));
		} catch (e) {
			// If sonner isn't mounted yet (e.g. error during bootstrap
			// before the Toaster component renders), the toast call is a
			// no-op. The console.error above still surfaces the error.
			console.warn("[renderer:globalErrorHandler] toast.error failed:", e);
		} // Do NOT call event.preventDefault(), we want the default
		// browser console error to also appear in DevTools for parity
		// with the pre-listener behavior.
	};
	window.addEventListener("error", onError);

	// Promise rejections with no ``.catch()`` handler.
	const onUnhandledRejection = (event: PromiseRejectionEvent) => {
		const detail = _formatForConsole(event.reason);
		console.error(
			"[renderer:globalErrorHandler] unhandled promise rejection:",
			detail,
		);
		// MO-102: persist to the host log (no-op where the bridge is
		// absent), before the toast (same ordering rationale as above).
		// A rejection carries no source position, so no `source` arg.
		_persistForTests.persist("unhandledrejection", event.reason);
		try {
			toast.error(_genericUserMessage(), _buildToastOptions(detail));
		} catch (e) {
			// Same defensive guard as above.
			console.warn(
				"[renderer:globalErrorHandler] toast.error (rejection) failed:",
				e,
			);
		}
		// Do NOT call event.preventDefault(), let the default browser
		// warning appear in DevTools too.
	};
	window.addEventListener("unhandledrejection", onUnhandledRejection);
	_installedHandlers = { onError, onUnhandledRejection };
}

/**
 * Test-only: reset the "installed" flag so unit tests can re-install
 * the handlers in a clean state.
 *
 * Not part of the public API; exported only for test isolation.
 */
export function _resetGlobalErrorHandlerStateForTests(): void {
	_installed = false;
	// Remove the listeners too. Resetting only the flag would let the
	// NEXT install add a SECOND pair while the earlier pair stayed
	// attached to the shared jsdom `window`, so every dispatched event
	// fired the persistence seam twice (test flake, and a misleading
	// "called 2 times" failure). Removing them keeps each test's
	// install/observe cycle isolated. No-op in production (the reset is
	// only ever called by tests).
	if (_installedHandlers && typeof window !== "undefined") {
		window.removeEventListener("error", _installedHandlers.onError);
		window.removeEventListener(
			"unhandledrejection",
			_installedHandlers.onUnhandledRejection,
		);
	}
	_installedHandlers = null;
}
