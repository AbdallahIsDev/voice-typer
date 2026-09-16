/**
 * Tests for `lib/console-capture.ts` (MO-105).
 *
 * Pins the Electron console-routing contract under Tauri: WARN and ERROR
 * are forwarded to the host sink, INFO/DEBUG never are, the original
 * console behavior is preserved, and the forwarded volume is bounded.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	_captureForTests,
	_resetConsoleCaptureForTests,
	CAPTURE_WINDOW_MS,
	installConsoleCapture,
	MAX_FORWARDS_PER_WINDOW,
	SUPPRESSION_NOTICE_PREFIX,
} from "@/lib/console-capture";

let sendSpy: ReturnType<typeof vi.spyOn>;
let originalWarn: typeof console.warn;
let originalError: typeof console.error;

beforeEach(() => {
	_resetConsoleCaptureForTests();
	// Snapshot the pristine console methods; `installConsoleCapture`
	// replaces them, so restoring between tests keeps the environment
	// clean for every other suite in the same worker.
	originalWarn = console.warn;
	originalError = console.error;
	// Silence the captured methods for the duration of a test: the
	// wrapper always re-emits to the original console (asserted
	// explicitly below), so without this the runner echoes every
	// captured record.
	console.warn = vi.fn();
	console.error = vi.fn();
	sendSpy = vi.spyOn(_captureForTests, "send").mockReturnValue(undefined);
});

afterEach(() => {
	console.warn = originalWarn;
	console.error = originalError;
	sendSpy.mockRestore();
	vi.restoreAllMocks();
	_resetConsoleCaptureForTests();
});

describe("installConsoleCapture", () => {
	it("forwards console.warn and console.error at their own level", () => {
		installConsoleCapture();

		console.warn("a warning", { k: 1 });
		console.error("an error");

		expect(sendSpy).toHaveBeenCalledTimes(2);
		expect(sendSpy.mock.calls[0]?.[0]).toBe("warn");
		expect(String(sendSpy.mock.calls[0]?.[1])).toContain("a warning");
		expect(sendSpy.mock.calls[1]?.[0]).toBe("error");
		expect(sendSpy.mock.calls[1]?.[1]).toBe("an error");
	});

	it("preserves the original console output", () => {
		const warnPassthrough = vi.fn();
		const errorPassthrough = vi.fn();
		console.warn = warnPassthrough;
		console.error = errorPassthrough;

		installConsoleCapture();
		console.warn("visible in devtools", 1);
		console.error("also visible");

		expect(warnPassthrough).toHaveBeenCalledWith("visible in devtools", 1);
		expect(errorPassthrough).toHaveBeenCalledWith("also visible");
	});

	it("never forwards INFO/DEBUG/log output", () => {
		const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
		const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
		const debug = vi
			.spyOn(console, "debug")
			.mockImplementation(() => undefined);

		installConsoleCapture();

		console.log("verbose");
		console.info("informational");
		console.debug("debug");

		expect(sendSpy).not.toHaveBeenCalled();
		expect([log, info, debug].every((spy) => spy.mock.calls.length === 1)).toBe(
			true,
		);
	});

	it("bounds forwarded volume per window and reports the truncation once", () => {
		installConsoleCapture();

		for (let i = 0; i < MAX_FORWARDS_PER_WINDOW + 10; i += 1) {
			console.warn(`flood ${i}`);
		}

		const messages = sendSpy.mock.calls.map((call: unknown[]) =>
			String(call[1] ?? ""),
		);
		const notices = messages.filter((message: string) =>
			message.startsWith(SUPPRESSION_NOTICE_PREFIX),
		);
		expect(messages.length - notices.length).toBe(MAX_FORWARDS_PER_WINDOW);
		expect(notices).toHaveLength(1);

		// A later window re-opens the budget.
		const base = Date.now();
		vi.spyOn(Date, "now").mockReturnValue(base + CAPTURE_WINDOW_MS + 1);
		console.warn("after the window");

		const after = sendSpy.mock.calls.map((call: unknown[]) =>
			String(call[1] ?? ""),
		);
		expect(after.at(-1)).toBe("after the window");
	});

	it("is idempotent (installing twice does not double-forward)", () => {
		installConsoleCapture();
		installConsoleCapture();

		console.error("once");

		expect(sendSpy).toHaveBeenCalledTimes(1);
	});

	it("truncates very long records instead of shipping them whole", () => {
		installConsoleCapture();

		console.error("x".repeat(50_000));

		const payload = String(sendSpy.mock.calls[0]?.[1] ?? "");
		expect(payload.endsWith("...[truncated]")).toBe(true);
		expect(payload.length).toBeLessThan(3_000);
		expect(payload.startsWith(SUPPRESSION_NOTICE_PREFIX)).toBe(false);
	});

	it("never throws when the bridge is absent", () => {
		sendSpy.mockReturnValue(undefined);
		installConsoleCapture();

		expect(() => console.error("no bridge here")).not.toThrow();
	});
});
