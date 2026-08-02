// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

const loggingSpies = vi.hoisted(() => ({
	appendLogLine: vi.fn(),
	rendererErrorsLogPath: vi.fn(() => "/tmp/electron-renderer-errors.log"),
}));

vi.mock("../../logging", () => loggingSpies);

import { appendRendererError } from "../renderer-error-persistence";

describe("appendRendererError", () => {
	afterEach(() => {
		vi.clearAllMocks();
		// Restores the console.warn spy from the error-swallow test even
		// if an assertion throws before the test body reaches a restore.
		vi.restoreAllMocks();
	});

	it("appends the line to the renderer-errors log", () => {
		appendRendererError("boom");

		expect(loggingSpies.rendererErrorsLogPath).toHaveBeenCalled();
		expect(loggingSpies.appendLogLine).toHaveBeenCalledWith(
			"/tmp/electron-renderer-errors.log",
			"boom",
		);
	});

	it("swallows I/O errors without throwing", () => {
		loggingSpies.appendLogLine.mockImplementation(() => {
			throw new Error("EACCES");
		});
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

		expect(() => appendRendererError("boom")).not.toThrow();
		expect(warnSpy).toHaveBeenCalled();
	});
});
