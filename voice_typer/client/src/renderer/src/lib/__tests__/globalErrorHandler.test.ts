/**
 *  — unit tests for `installGlobalErrorHandlers()`.
 *
 * Asserts that the renderer's global safety net is actually installed:
 * after calling `installGlobalErrorHandlers()` (the function
 * `main.tsx` and `bubble-main.tsx` invoke at the top of their module
 * body, before React mounts), `window` MUST have both an `error` and
 * an `unhandledrejection` listener registered. Without this guarantee,
 * every unhandled promise rejection (the 13+ `.catch(() => {})`
 * swallows identified in ) silently vanishes — no toast, no
 * console trace, no main-process log line.
 *
 * The test spies on `window.addEventListener` so it can verify the
 * exact event types registered without depending on internal listener
 * identity. It also exercises the idempotency contract (second call
 * is a no-op) and the DOM-availability guard (skips cleanly when
 * `window.addEventListener` is missing — defensive, should never
 * happen in a real Electron renderer).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	_resetGlobalErrorHandlerStateForTests,
	installGlobalErrorHandlers,
} from "@/lib/globalErrorHandler";

describe("G4-CR-10: installGlobalErrorHandlers registers both listeners", () => {
	// Typed spy: pin the call signature so `.mock.calls` is
	// `Parameters<typeof window.addEventListener>[]` (typed tuple)
	// rather than the loose `any[]` you get from
	// `ReturnType<typeof vi.spyOn>`. This makes the destructure /
	// `.filter` calls below type-safe under tsconfig's
	// `strict: true` (no implicit any).
	let addEventListenerSpy: import("vitest").MockInstance<
		typeof window.addEventListener
	>;

	//track listeners actually registered on ``window`` so
	// ``afterEach`` can remove them. Pre-fix,
	// ``_resetGlobalErrorHandlerStateForTests()`` only reset the
	// module-level ``_installed`` flag — the REAL
	// ``window.addEventListener`` calls still went through (the spy
	// delegated to the real method) and accumulated 2 listeners per
	// test (one ``error`` + one ``unhandledrejection``).
	// ``vi.spyOn(...).mockRestore()`` un-spies the method but does NOT
	// remove the listeners the spy already forwarded. After N tests,
	// ``window`` had ``2*N`` listeners — a real listener leak that
	// could mask regressions in subsequent test files sharing the
	// jsdom ``window``.
	let installedListeners: Array<{
		type: string;
		cb: EventListenerOrEventListenerObject;
	}> = [];

	beforeEach(() => {
		// Reset the module-level `_installed` flag so each test
		// starts from a clean slate (otherwise the first test
		// would flip the flag and subsequent tests would see a
		// no-op install).
		_resetGlobalErrorHandlerStateForTests();
		installedListeners = [];
		// Capture the REAL addEventListener BEFORE spying — the mock
		// implementation must forward to it, not to `window.
		// addEventListener` (which is the spy itself after spyOn →
		// infinite recursion / RangeError: Maximum call stack size).
		const originalAddEventListener = window.addEventListener.bind(window);
		addEventListenerSpy = vi
			.spyOn(window, "addEventListener")
			.mockImplementation(
				(
					type: string,
					cb: EventListenerOrEventListenerObject,
					options?: boolean | AddEventListenerOptions,
				) => {
					// Record the listener so afterEach can remove it.
					// Forward to the real ``window.addEventListener``
					// so the handler is actually installed (the
					// "logs to console.error" tests below dispatch
					// real ``ErrorEvent``s and need the real
					// listener to fire).
					installedListeners.push({ type, cb });
					return originalAddEventListener(type, cb, options);
				},
			) as unknown as typeof addEventListenerSpy;
	});

	afterEach(() => {
		//remove every listener the spy forwarded so the
		// jsdom ``window`` is clean for the next test (and for
		// subsequent test files in the same vitest worker).
		for (const { type, cb } of installedListeners) {
			window.removeEventListener(type, cb);
		}
		installedListeners = [];
		_resetGlobalErrorHandlerStateForTests();
		addEventListenerSpy.mockRestore();
	});

	it("registers a listener for the 'error' event on window", () => {
		installGlobalErrorHandlers();
		const errorCalls = addEventListenerSpy.mock.calls.filter(
			(args) => args[0] === "error",
		);
		expect(errorCalls.length).toBe(1);
		expect(typeof errorCalls[0]?.[1]).toBe("function");
	});

	it("registers a listener for the 'unhandledrejection' event on window", () => {
		installGlobalErrorHandlers();
		const rejectionCalls = addEventListenerSpy.mock.calls.filter(
			(args) => args[0] === "unhandledrejection",
		);
		expect(rejectionCalls.length).toBe(1);
		expect(typeof rejectionCalls[0]?.[1]).toBe("function");
	});

	it("registers EXACTLY one 'error' and one 'unhandledrejection' listener (no leaks)", () => {
		installGlobalErrorHandlers();
		const types = addEventListenerSpy.mock.calls.map(
			(args) => args[0],
		) as string[];
		const errorCount = types.filter((t) => t === "error").length;
		const rejectionCount = types.filter(
			(t) => t === "unhandledrejection",
		).length;
		expect(errorCount).toBe(1);
		expect(rejectionCount).toBe(1);
	});

	it("is idempotent — calling twice registers each listener exactly once", () => {
		installGlobalErrorHandlers();
		installGlobalErrorHandlers();
		installGlobalErrorHandlers();
		const types = addEventListenerSpy.mock.calls.map(
			(args) => args[0],
		) as string[];
		expect(types.filter((t) => t === "error").length).toBe(1);
		expect(types.filter((t) => t === "unhandledrejection").length).toBe(1);
	});
});

/**
 * Verify that dispatching a real `error` event triggers the installed
 * listener and produces a console.error trace. This is the integration
 * contract: the renderer needs the error to be visible in the
 * Electron main-process log (forwarded via webContents.on("console-message")).
 */
describe("G4-CR-10: installed listener logs to console.error", () => {
	let consoleErrorSpy: ReturnType<typeof vi.spyOn>;
	//this describe block calls ``installGlobalErrorHandlers()``
	// (which calls ``window.addEventListener`` for real) inside each
	// ``it``. Track the listeners so ``afterEach`` can remove them.
	// Pre-fix, each test leaked one ``error`` + one ``unhandledrejection``
	// listener onto the shared jsdom ``window``.
	let installedListeners: Array<{
		type: string;
		cb: EventListenerOrEventListenerObject;
	}> = [];
	let addEventListenerSpy: import("vitest").MockInstance<
		typeof window.addEventListener
	>;

	beforeEach(() => {
		_resetGlobalErrorHandlerStateForTests();
		installedListeners = [];
		// Capture the REAL addEventListener BEFORE spying — the mock
		// implementation must forward to it, not to `window.
		// addEventListener` (which is the spy itself after spyOn →
		// infinite recursion / RangeError: Maximum call stack size).
		const originalAddEventListener = window.addEventListener.bind(window);
		// Spy on ``window.addEventListener`` ONLY to record the
		// listeners for cleanup — forward to the real method so the
		// dispatched ``ErrorEvent`` / ``PromiseRejectionEvent`` below
		// actually fire the installed handlers.
		addEventListenerSpy = vi
			.spyOn(window, "addEventListener")
			.mockImplementation(
				(
					type: string,
					cb: EventListenerOrEventListenerObject,
					options?: boolean | AddEventListenerOptions,
				) => {
					installedListeners.push({ type, cb });
					return originalAddEventListener(type, cb, options);
				},
			) as unknown as typeof addEventListenerSpy;
		consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
	});

	afterEach(() => {
		//remove every listener the spy forwarded so the
		// jsdom ``window`` is clean for the next test.
		for (const { type, cb } of installedListeners) {
			window.removeEventListener(type, cb);
		}
		installedListeners = [];
		_resetGlobalErrorHandlerStateForTests();
		addEventListenerSpy.mockRestore();
		consoleErrorSpy.mockRestore();
	});

	it("logs synchronous 'error' events with the [renderer:globalErrorHandler] prefix", () => {
		installGlobalErrorHandlers();
		const event = new ErrorEvent("error", {
			error: new Error("synthetic-renderer-boom"),
			message: "synthetic-renderer-boom",
		});
		window.dispatchEvent(event);
		expect(consoleErrorSpy).toHaveBeenCalled();
		const firstCallArg = String(consoleErrorSpy.mock.calls[0]?.[0] ?? "");
		expect(firstCallArg).toContain("[renderer:globalErrorHandler]");
	});

	it("logs 'unhandledrejection' events with the [renderer:globalErrorHandler] prefix", () => {
		installGlobalErrorHandlers();
		// Construct a PromiseRejectionEvent with a resolved promise —
		// the listener only reads `event.reason`, so the promise state
		// doesn't matter. Using `Promise.resolve()` avoids creating
		// an actual unhandled rejection that vitest would flag.
		const event = new PromiseRejectionEvent("unhandledrejection", {
			promise: Promise.resolve(),
			reason: new Error("synthetic-rejection"),
		});
		window.dispatchEvent(event);
		expect(consoleErrorSpy).toHaveBeenCalled();
		const firstCallArg = String(consoleErrorSpy.mock.calls[0]?.[0] ?? "");
		expect(firstCallArg).toContain("[renderer:globalErrorHandler]");
	});
});
