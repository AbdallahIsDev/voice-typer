// @vitest-environment node
/**
 * Behavioral tests for the dashboard window's renderer console
 * telemetry (`windows/renderer-telemetry.ts`).
 *
 * The module owns TWO responsibilities that must stay decoupled:
 *   1. Forwarding renderer console output to the structured
 *      main-process logger (INFO/WARN/ERROR routing, gated at
 *      level >= 1, `[MAIN renderer]` tag, RENDERER_CLR color).
 *   2. Persisting ERROR-level renderer output (PII-redacted) to
 *      `electron-renderer-errors.log` via `appendRendererError`.
 *
 * The forwarding half is shared with the bubble window —
 * `attachConsoleForwarder` (bubble/console-forwarder.ts) is the
 * single implementation; this module must delegate to it (tag
 * `"[MAIN renderer]"`, color `RENDERER_CLR`) instead of carrying a
 * parallel copy. These tests pin the exact byte format of both the
 * forwarded log lines and the persisted error lines so the
 * delegation can never change observable output, and guard that
 * `cleanConsoleMsg` runs exactly once per event (the duplicated
 * inline copy used to run it twice for every ERROR line: once for
 * the forwarded message and once for the persisted line).
 *
 * All logging dependencies are mocked, no real log files, no real
 * Electron.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

const logSpies = vi.hoisted(() => ({
	info: vi.fn(),
	warn: vi.fn(),
	error: vi.fn(),
	debug: vi.fn(),
}));

const cleanConsoleMsgSpy = vi.hoisted(() => vi.fn((m: unknown) => String(m)));

const fileTimestampStub = vi.hoisted(() => vi.fn(() => "2026-08-30  10:00:00"));

const redactPiiSpy = vi.hoisted(() => vi.fn((s: string) => `[REDACTED:${s}]`));

const appendRendererErrorSpy = vi.hoisted(() => vi.fn());

vi.mock("../../logging", () => ({
	log: logSpies,
	RESET: "\u001b[0m",
	RENDERER_CLR: "\u001b[38;5;227m",
	cleanConsoleMsg: cleanConsoleMsgSpy,
	fileTimestamp: fileTimestampStub,
	redactPii: redactPiiSpy,
}));

vi.mock("../renderer-error-persistence", () => ({
	appendRendererError: appendRendererErrorSpy,
}));

import type { BrowserWindow } from "electron";
import { registerRendererTelemetry } from "../renderer-telemetry";

const RENDERER_CLR = "\u001b[38;5;227m";
const RESET = "\u001b[0m";

interface MockConsoleEvent {
	level: number;
	message: string;
	lineNumber: number;
	sourceId: string;
}

function setup() {
	let handler: ((e: MockConsoleEvent) => void) | undefined;
	const onSpy = vi.fn((_event: string, cb: (e: MockConsoleEvent) => void) => {
		handler = cb;
	});
	const win = {
		webContents: {
			on: onSpy,
		},
	} as unknown as BrowserWindow;
	registerRendererTelemetry(win);
	return {
		onSpy,
		emit: (e: MockConsoleEvent) => handler?.(e),
	};
}

describe("registerRendererTelemetry, forwarded log lines (byte-exact)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("registers exactly one console-message listener (forwarder + persistence share it)", () => {
		const { onSpy } = setup();
		const consoleMessageCalls = onSpy.mock.calls.filter(
			(c) => c[0] === "console-message",
		);
		expect(consoleMessageCalls).toHaveLength(1);
	});

	it("forwards INFO (level 1) through log.info with the exact main-renderer line format", () => {
		const { emit } = setup();
		emit({
			level: 1,
			message: "renderer booted",
			lineNumber: 12,
			sourceId: "http://localhost:5173/src/main.tsx",
		});
		expect(logSpies.info).toHaveBeenCalledTimes(1);
		expect(logSpies.info).toHaveBeenCalledWith(
			`${RENDERER_CLR}[MAIN renderer] INFO${RESET} renderer booted (http://localhost:5173/src/main.tsx:12)`,
		);
		expect(logSpies.warn).not.toHaveBeenCalled();
		expect(logSpies.error).not.toHaveBeenCalled();
	});

	it("forwards WARN (level 2) through log.warn with the exact main-renderer line format", () => {
		const { emit } = setup();
		emit({ level: 2, message: "careful", lineNumber: 7, sourceId: "a.ts" });
		expect(logSpies.warn).toHaveBeenCalledTimes(1);
		expect(logSpies.warn).toHaveBeenCalledWith(
			`${RENDERER_CLR}[MAIN renderer] WARN${RESET} careful (a.ts:7)`,
		);
		expect(logSpies.info).not.toHaveBeenCalled();
		expect(logSpies.error).not.toHaveBeenCalled();
	});

	it("forwards ERROR (level 3) through log.error with the exact main-renderer line format", () => {
		const { emit } = setup();
		emit({
			level: 3,
			message: "Uncaught TypeError: boom",
			lineNumber: 88,
			sourceId: "app.chunk.js",
		});
		expect(logSpies.error).toHaveBeenCalledTimes(1);
		expect(logSpies.error).toHaveBeenCalledWith(
			`${RENDERER_CLR}[MAIN renderer] ERROR${RESET} Uncaught TypeError: boom (app.chunk.js:88)`,
		);
		expect(logSpies.info).not.toHaveBeenCalled();
		expect(logSpies.warn).not.toHaveBeenCalled();
	});

	it("tags unknown levels as LOG and routes them through log.error (level >= 3)", () => {
		const { emit } = setup();
		emit({ level: 9, message: "weird", lineNumber: 3, sourceId: "d.ts" });
		expect(logSpies.error).toHaveBeenCalledTimes(1);
		expect(logSpies.error).toHaveBeenCalledWith(
			`${RENDERER_CLR}[MAIN renderer] LOG${RESET} weird (d.ts:3)`,
		);
	});

	it("drops VERBOSE (level 0), no log call, no cleanConsoleMsg, no persistence", () => {
		const { emit } = setup();
		emit({ level: 0, message: "noisy", lineNumber: 1, sourceId: "v.ts" });
		expect(logSpies.info).not.toHaveBeenCalled();
		expect(logSpies.warn).not.toHaveBeenCalled();
		expect(logSpies.error).not.toHaveBeenCalled();
		expect(cleanConsoleMsgSpy).not.toHaveBeenCalled();
		expect(appendRendererErrorSpy).not.toHaveBeenCalled();
	});

	it("cleans the raw renderer message before forwarding (printf specifiers / %c styles)", () => {
		const { emit } = setup();
		emit({
			level: 1,
			message: "%c color style %s",
			lineNumber: 5,
			sourceId: "s.ts",
		});
		expect(cleanConsoleMsgSpy).toHaveBeenCalledWith("%c color style %s");
	});
});

describe("registerRendererTelemetry, ERROR persistence (byte-exact)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("persists ERROR (level 3) lines with the exact renderer-error format, PII-redacted", () => {
		const { emit } = setup();
		emit({
			level: 3,
			message: "Uncaught TypeError: boom",
			lineNumber: 88,
			sourceId: "app.chunk.js",
		});
		expect(appendRendererErrorSpy).toHaveBeenCalledTimes(1);
		expect(appendRendererErrorSpy).toHaveBeenCalledWith(
			"2026-08-30  10:00:00  ERROR  [renderer-error] [REDACTED:Uncaught TypeError: boom] (app.chunk.js:88)\n",
		);
		// The redaction runs on the CLEANED text (printf specifiers
		// stripped first, PII patterns second).
		expect(redactPiiSpy).toHaveBeenCalledWith("Uncaught TypeError: boom");
		// Ordering is preserved from the legacy inline handler: the
		// forwarded log.error fires BEFORE the persistence append.
		const errorOrder = logSpies.error.mock.invocationCallOrder[0];
		const persistOrder = appendRendererErrorSpy.mock.invocationCallOrder[0];
		expect(errorOrder).toBeDefined();
		expect(persistOrder).toBeDefined();
		expect(errorOrder).toBeLessThan(persistOrder as number);
	});

	it("persists unknown levels >= 3 (LOG-tagged errors are still errors)", () => {
		const { emit } = setup();
		emit({ level: 9, message: "weird", lineNumber: 3, sourceId: "d.ts" });
		expect(appendRendererErrorSpy).toHaveBeenCalledTimes(1);
		expect(appendRendererErrorSpy).toHaveBeenCalledWith(
			"2026-08-30  10:00:00  ERROR  [renderer-error] [REDACTED:weird] (d.ts:3)\n",
		);
	});

	it("does NOT persist INFO/WARN lines", () => {
		const { emit } = setup();
		emit({ level: 1, message: "info msg", lineNumber: 1, sourceId: "i.ts" });
		emit({ level: 2, message: "warn msg", lineNumber: 2, sourceId: "w.ts" });
		expect(appendRendererErrorSpy).not.toHaveBeenCalled();
		expect(redactPiiSpy).not.toHaveBeenCalled();
	});
});

describe("registerRendererTelemetry, single cleanConsoleMsg pass per event", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("cleans each ERROR message exactly once (no double cleanConsoleMsg per line)", () => {
		// The legacy inline copy called cleanConsoleMsg twice for every
		// ERROR event: once to build the forwarded message and once to
		// build the persisted line. The shared forwarder must clean once
		// and hand the cleaned text to the ERROR-persistence sink.
		const { emit } = setup();
		emit({ level: 3, message: "boom", lineNumber: 1, sourceId: "a.js" });
		expect(cleanConsoleMsgSpy).toHaveBeenCalledTimes(1);
		expect(cleanConsoleMsgSpy).toHaveBeenCalledWith("boom");
	});

	it("cleans each INFO message exactly once", () => {
		const { emit } = setup();
		emit({ level: 1, message: "hello", lineNumber: 1, sourceId: "a.js" });
		expect(cleanConsoleMsgSpy).toHaveBeenCalledTimes(1);
	});
});

describe("registerRendererTelemetry, delegates to the shared console forwarder", () => {
	it("source: imports and calls attachConsoleForwarder (no inline level-routing copy)", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../renderer-telemetry.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{\s*attachConsoleForwarder\s*\}\s+from\s+["']\.\/bubble\/console-forwarder["']/,
		);
		expect(src).toMatch(/attachConsoleForwarder\(\s*win\s*,/);
		// The duplicated inline forwarding body must be gone, the
		// level-routing if/else chain is the shared helper's job now.
		expect(src).not.toMatch(/if \(level >= 3\) log\.error\(msg\)/);
		expect(src).not.toMatch(/else if \(level === 2\) log\.warn\(msg\)/);
	});

	it("source: forwards with the [MAIN renderer] tag + RENDERER_CLR color so output is byte-identical to the bubble helper under parameter substitution", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../renderer-telemetry.ts"),
			"utf-8",
		);
		expect(src).toContain('"[MAIN renderer]"');
		expect(src).toContain("RENDERER_CLR");
	});
});
