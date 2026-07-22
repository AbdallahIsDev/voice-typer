/**
 * G4-CR-10 — unit tests for `installGlobalErrorHandlers()`.
 *
 * Asserts that the renderer's global safety net is actually installed:
 * after calling `installGlobalErrorHandlers()` (the function
 * `main.tsx` and `bubble-main.tsx` invoke at the top of their module
 * body, before React mounts), `window` MUST have both an `error` and
 * an `unhandledrejection` listener registered. Without this guarantee,
 * every unhandled promise rejection (the 13+ `.catch(() => {})`
 * swallows identified in G4-H-25) silently vanishes — no toast, no
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

	beforeEach(() => {
		// Reset the module-level `_installed` flag so each test
		// starts from a clean slate (otherwise the first test
		// would flip the flag and subsequent tests would see a
		// no-op install).
		_resetGlobalErrorHandlerStateForTests();
		addEventListenerSpy = vi.spyOn(
			window,
			"addEventListener",
		) as unknown as typeof addEventListenerSpy;
	});

	afterEach(() => {
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

	beforeEach(() => {
		_resetGlobalErrorHandlerStateForTests();
		consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
	});

	afterEach(() => {
		_resetGlobalErrorHandlerStateForTests();
		consoleErrorSpy.mockRestore();
	});

	it("logs synchronous 'error' events with the [Renderer] prefix", () => {
		installGlobalErrorHandlers();
		const event = new ErrorEvent("error", {
			error: new Error("synthetic-renderer-boom"),
			message: "synthetic-renderer-boom",
		});
		window.dispatchEvent(event);
		expect(consoleErrorSpy).toHaveBeenCalled();
		const firstCallArg = String(consoleErrorSpy.mock.calls[0]?.[0] ?? "");
		expect(firstCallArg).toContain("[Renderer]");
	});

	it("logs 'unhandledrejection' events with the [Renderer] prefix", () => {
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
		expect(firstCallArg).toContain("[Renderer]");
	});
});
