/**
 * Pins the bubble entrypoint's observability installs (MO-102 + MO-105).
 * The bubble is a SEPARATE BrowserWindow / JS context from the main
 * renderer. Under predecessor the main process captured console output and
 * grant (SEC-026: observability OUT only).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const BUBBLE_SRC = readFileSync(
	resolve(__dirname, "../bubble-main.tsx"),
	"utf-8",
);
const MAIN_SRC = readFileSync(resolve(__dirname, "../main.tsx"), "utf-8");

describe("bubble-main.tsx installs both host-log sinks", () => {
	it("imports and calls installGlobalErrorHandlers (MO-102)", () => {
		expect(BUBBLE_SRC).toMatch(
			/import\s*\{\s*installGlobalErrorHandlers\s*\}\s*from\s*["']\.\/lib\/globalErrorHandler["']/,
		);
		expect(BUBBLE_SRC).toMatch(/installGlobalErrorHandlers\(\)/);
	});

	it("imports and calls installConsoleCapture (MO-105 bubble context)", () => {
		// entrypoint's `console.warn`/`console.error` were lost under
		// Tauri (separate JS context, no predecessor console-message hook).
		expect(BUBBLE_SRC).toMatch(
			/import\s*\{\s*installConsoleCapture\s*\}\s*from\s*["']\.\/lib\/console-capture["']/,
		);
		expect(BUBBLE_SRC).toMatch(/installConsoleCapture\(\)/);
	});

	it("mirrors main.tsx: both sinks installed from both entrypoints", () => {
		for (const src of [MAIN_SRC, BUBBLE_SRC]) {
			expect(src).toMatch(/installGlobalErrorHandlers\(\)/);
			expect(src).toMatch(/installConsoleCapture\(\)/);
		}
	});
});
